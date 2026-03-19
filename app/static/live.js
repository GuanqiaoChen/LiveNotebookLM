/**
 * LiveNotebookLM – production frontend
 *
 * Audio pipeline:
 *   Mic → AudioWorkletNode (audio thread) → postMessage →
 *   main thread: downsample → PCM-16 → base64 → WebSocket → Gemini Live
 *
 * Playback:
 *   WebSocket → base64 → PCM-16 → Float32 → AudioBufferSource
 */

const API_BASE = window.location.origin;
const WS_BASE  = window.location.origin.replace(/^http/, "ws");

// ── Per-browser identity ──────────────────────────────────────────────────────
// A UUID generated on first visit and persisted in localStorage.
// Every API request carries it as X-Client-ID so each visitor gets their own
// isolated session workspace with no data shared across browsers.

const CLIENT_ID = (() => {
  let id = localStorage.getItem("lnlm_client_id");
  if (!id) {
    id = crypto.randomUUID();
    localStorage.setItem("lnlm_client_id", id);
  }
  return id;
})();

// Thin wrapper around fetch() that automatically attaches X-Client-ID.
function apiFetch(url, options = {}) {
  const headers = { "X-Client-ID": CLIENT_ID, ...(options.headers || {}) };
  return fetch(url, { ...options, headers });
}
const ASSISTANT_PCM_RATE = 24000;
const USER_PCM_RATE      = 16000;

// ── State ────────────────────────────────────────────────────────────────────

const state = {
  ws: null,
  sessionId: "",
  runtimeReady: false,
  conversationActive: false,
  startupFailed: false,
  sessionSourceCount: 0,
  recapData: null,
  currentSources: [],   // tracks uploaded sources for web-result restore
  titleSet: false,      // true once session title has been auto-set

  // Web Audio
  audioContext: null,
  playbackTime: 0,
  activeAudioNodes: [],

  // Microphone (AudioWorklet primary, ScriptProcessor fallback)
  mediaStream: null,
  mediaSourceNode: null,
  workletNode: null,
  processorNode: null,
  _lastSpeechAt: 0,        // timestamp of last audio chunk above energy threshold
  _lastKeepaliveAt: 0,     // timestamp of last keepalive audio chunk sent during silence
  _interruptSpeechStart: 0, // timestamp when sustained speech above interrupt threshold began
  _locallyInterrupted: false, // true after local interrupt fires; cleared on server ack

  // Live transcript bubbles
  liveUserBubble: null,
  liveAssistantBubble: null,
  currentUserText: "",
  currentAssistantText: "",

  // Pending user bubble: held for 350 ms after turn_complete to catch
  // late-arriving final ASR transcripts from Gemini before we finalise
  pendingUserBubble: null,
  _pendingUserBubbleTimer: null,
};

// Web search results: [{ title, url, snippet, checked }]
const webState = { results: [], searching: false };

// ── DOM refs ─────────────────────────────────────────────────────────────────

const els = {
  // Session
  newChatBtn:       document.getElementById("newChatBtn"),
  sessionListWrap:  document.getElementById("sessionListWrap"),
  // Conversation
  beginBtn:         document.getElementById("beginConvBtn"),
  endBtn:           document.getElementById("endConvBtn"),
  statusBox:        document.getElementById("statusBox"),
  micIndicator:     document.getElementById("micIndicator"),
  messages:         document.getElementById("messages"),
  // Citations
  sourceCues:       document.getElementById("sourceCues"),
  // Sources
  sourceList:       document.getElementById("sourceList"),
  sourceCount:      document.getElementById("sourceCount"),
  sourceListError:  document.getElementById("sourceListError"),
  fileInput:        document.getElementById("fileUploadInput"),
  uploadLabel:      document.getElementById("uploadLabel"),
  // Web search
  webSearchInput:   document.getElementById("webSearchInput"),
  webSearchBtn:     document.getElementById("webSearchBtn"),
  webResultsList:   document.getElementById("webResultsList"),
  addWebBtn:        document.getElementById("addWebBtn"),
  webSearchError:   document.getElementById("webSearchError"),
  // Recap
  generateRecapBtn: document.getElementById("generateRecapBtn"),
  downloadRecapBtn: document.getElementById("downloadRecapBtn"),
  recapPreview:     document.getElementById("recapPreview"),
};

// ── UI helpers ───────────────────────────────────────────────────────────────

function setStatus(text) { els.statusBox.textContent = text; }

function setMicActive(on) {
  els.micIndicator.className = "mic-dot " + (on ? "mic-on" : "mic-off");
}

function updateButtons() {
  const active = state.conversationActive;
  els.beginBtn.disabled = active || !state.sessionId;
  els.endBtn.disabled   = !active;
}

function showSourceError(msg) {
  els.sourceListError.textContent = msg;
  els.sourceListError.style.display = msg ? "" : "none";
}

function showWebError(msg) {
  els.webSearchError.textContent = msg;
  els.webSearchError.style.display = msg ? "" : "none";
}

function createLiveBubble(role) {
  const div = document.createElement("div");
  div.className = `msg ${role} bubble-live`;
  div.textContent = "…";
  els.messages.appendChild(div);
  scrollMessages();
  return div;
}

function finaliseBubble(bubble, text) {
  if (!bubble) return;
  if (text && text.trim()) {
    bubble.classList.remove("bubble-live");
    bubble.textContent = text.trim();
  } else {
    bubble.remove();
  }
}

function addSystemMessage(text) {
  const div = document.createElement("div");
  div.className = "msg system";
  div.textContent = text;
  els.messages.appendChild(div);
  scrollMessages();
}

function scrollMessages() {
  els.messages.scrollTop = els.messages.scrollHeight;
}

// ── Session list (ChatGPT-style) ─────────────────────────────────────────────

async function loadSessionList() {
  try {
    const res = await apiFetch(`${API_BASE}/sessions`);
    if (!res.ok) return;
    const sessions = await res.json();
    renderSessionList(sessions);
  } catch (_) {}
}

function renderSessionList(sessions) {
  const wrap = els.sessionListWrap;
  wrap.innerHTML = "";

  if (!sessions || !sessions.length) {
    wrap.innerHTML = '<div class="session-list-empty">No sessions yet.<br>Click "New Conversation" to start.</div>';
    return;
  }

  // Sort newest first
  sessions.sort((a, b) => new Date(b.updated_at) - new Date(a.updated_at));

  const now   = new Date();
  const today = new Date(now.getFullYear(), now.getMonth(), now.getDate());
  const yesterday = new Date(today - 86400000);
  const lastWeek  = new Date(today - 6 * 86400000);

  const groups = { Today: [], Yesterday: [], "Last 7 days": [], Earlier: [] };
  for (const s of sessions) {
    const d = new Date(s.updated_at);
    const day = new Date(d.getFullYear(), d.getMonth(), d.getDate());
    if (day >= today)         groups["Today"].push(s);
    else if (day >= yesterday) groups["Yesterday"].push(s);
    else if (day >= lastWeek)  groups["Last 7 days"].push(s);
    else                       groups["Earlier"].push(s);
  }

  for (const [label, items] of Object.entries(groups)) {
    if (!items.length) continue;

    const grp = document.createElement("div");
    grp.className = "session-group-label";
    grp.textContent = label;
    wrap.appendChild(grp);

    for (const s of items) {
      const item = document.createElement("div");
      item.className = "session-item" + (s.session_id === state.sessionId ? " active" : "");
      item.dataset.sessionId = s.session_id;

      const title = document.createElement("span");
      title.className = "session-item-title";
      title.textContent = s.title || "Untitled session";

      const cnt = document.createElement("span");
      cnt.className = "session-count";
      if (s.message_count) cnt.textContent = s.message_count;

      item.appendChild(title);
      item.appendChild(cnt);
      item.addEventListener("click", () => selectSession(s.session_id, s.title));
      wrap.appendChild(item);
    }
  }
}

// Switch the mobile tab bar to the given panel (no-op on desktop).
function switchMobileTab(tab, btn) {
  const layout = document.getElementById("appLayout");
  if (!layout) return;
  layout.dataset.tab = tab;
  document.querySelectorAll(".mobile-tab").forEach(b => b.classList.remove("active"));
  const target = btn || document.querySelector(`.mobile-tab[data-panel="${tab}"]`);
  if (target) target.classList.add("active");
}

async function selectSession(sessionId, title) {
  if (state.conversationActive) {
    if (!confirm("End the current conversation and switch sessions?")) return;
    await endConversation();
  }

  state.sessionId = sessionId;
  state.titleSet = true; // don't auto-title existing sessions

  // On mobile, jump to Chat panel so the user sees the conversation immediately.
  switchMobileTab("chat", null);

  // Highlight active session
  document.querySelectorAll(".session-item").forEach(el => {
    el.classList.toggle("active", el.dataset.sessionId === sessionId);
  });

  // Clear ALL per-session UI immediately and synchronously before any async
  // work begins — this is the authoritative reset point so stale data from the
  // previous session can never bleed through regardless of network timing.
  els.messages.innerHTML = "";
  state.recapData = null;
  renderRecapPreview(null);
  renderSources([]);

  setStatus(`Session: ${title || sessionId.slice(0, 8)}`);
  updateButtons();

  // Load session data in parallel
  await Promise.all([loadSources(), tryLoadExistingRecap()]);
  await loadSessionMessages(sessionId);
}

async function loadSessionMessages(sessionId) {
  try {
    const res = await apiFetch(`${API_BASE}/sessions/${sessionId}`);
    if (!res.ok) return;
    const data = await res.json();
    const messages = data.messages || [];
    if (!messages.length) return;

    for (const msg of messages) {
      if (!msg.content || !msg.content.trim()) continue;
      if (msg.role === "system") { addSystemMessage(msg.content); continue; }
      const div = document.createElement("div");
      div.className = `msg ${msg.role === "user" ? "user" : "assistant"}`;
      div.textContent = msg.content;
      els.messages.appendChild(div);
    }
    scrollMessages();
  } catch (_) {}
}

async function createSession(title, voice) {
  const res = await apiFetch(`${API_BASE}/sessions`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ title: title || "New conversation", voice: voice || "Aoede" }),
  });
  if (!res.ok) throw new Error(`Failed to create session: ${res.status}`);
  const data = await res.json();
  return data;
}

// ── Voice selection modal ────────────────────────────────────────────────────

const VOICE_DESCRIPTIONS = {
  Aoede:  "Warm and expressive. Natural, friendly tone. Great for long conversations.",
  Charon: "Deep and measured. Calm authority. Ideal for detailed analysis.",
  Fenrir: "Bold and energetic. Dynamic delivery. Best for engaging explanations.",
  Kore:   "Clear and precise. Professional tone. Perfect for structured summaries.",
  Puck:   "Light and playful. Upbeat and accessible. Great for casual exploration.",
  Orbit:  "Smooth and confident. Balanced presence. Works well for any topic.",
  Zephyr: "Gentle and soothing. Relaxed delivery. Excellent for nuanced discussion.",
};

let _voiceModalResolve = null;
let _previewAudioCtx = null;
let _previewSource = null;

function showVoiceModal() {
  return new Promise((resolve) => {
    _voiceModalResolve = resolve;

    const grid = document.getElementById("voiceGrid");
    const modal = document.getElementById("voiceModal");
    if (!grid || !modal) {
      resolve("Aoede");
      return;
    }
    grid.innerHTML = "";

    const voices = Object.keys(VOICE_DESCRIPTIONS);
    voices.forEach((name, i) => {
      const card = document.createElement("label");
      card.className = "voice-card" + (i === 0 ? " selected" : "");
      card.dataset.voice = name;
      card.innerHTML = `
        <input type="radio" name="voiceChoice" value="${name}"${i === 0 ? " checked" : ""}>
        <div class="voice-card-body">
          <div class="voice-card-name">${name}</div>
          <div class="voice-card-desc">${VOICE_DESCRIPTIONS[name]}</div>
        </div>
        <button type="button" class="voice-preview-btn" data-voice="${name}" title="Preview ${name}">
          ▶ Preview
        </button>
      `;
      card.addEventListener("click", (e) => {
        if (e.target.closest(".voice-preview-btn")) return; // handled separately
        grid.querySelectorAll(".voice-card").forEach(c => c.classList.remove("selected"));
        card.classList.add("selected");
        card.querySelector("input").checked = true;
      });
      grid.appendChild(card);
    });

    // Preview button clicks
    grid.addEventListener("click", (e) => {
      const btn = e.target.closest(".voice-preview-btn");
      if (!btn) return;
      e.preventDefault();
      _playVoicePreview(btn.dataset.voice, btn);
    });

    modal.classList.remove("hidden");
  });
}

async function _playVoicePreview(voiceName, btn) {
  if (btn.disabled) return;

  // Stop any in-progress preview
  if (_previewSource) {
    try { _previewSource.stop(); } catch (_) {}
    _previewSource = null;
  }
  document.querySelectorAll(".voice-preview-btn").forEach(b => {
    b.textContent = "▶ Preview";
    b.classList.remove("playing");
    b.disabled = false;
  });

  btn.textContent = "Loading…";
  btn.disabled = true;

  try {
    const res = await apiFetch(`${API_BASE}/voices/preview/${voiceName}`);
    if (!res.ok) throw new Error(`Preview failed: ${res.status}`);
    const arrayBuffer = await res.arrayBuffer();

    // Raw PCM s16le at 24 kHz — decode manually with Web Audio API
    const sampleRate = 24000;
    const samples = arrayBuffer.byteLength / 2;
    if (!_previewAudioCtx || _previewAudioCtx.state === "closed") {
      _previewAudioCtx = new AudioContext({ sampleRate });
    }
    const audioBuffer = _previewAudioCtx.createBuffer(1, samples, sampleRate);
    const channel = audioBuffer.getChannelData(0);
    const view = new DataView(arrayBuffer);
    for (let i = 0; i < samples; i++) {
      channel[i] = view.getInt16(i * 2, true) / 32768;
    }

    btn.textContent = "▶ Playing";
    btn.classList.add("playing");
    btn.disabled = false;

    const source = _previewAudioCtx.createBufferSource();
    _previewSource = source;
    source.buffer = audioBuffer;
    source.connect(_previewAudioCtx.destination);
    source.onended = () => {
      btn.textContent = "▶ Preview";
      btn.classList.remove("playing");
      _previewSource = null;
    };
    source.start();
  } catch (err) {
    btn.textContent = "▶ Preview";
    btn.disabled = false;
    console.warn("Voice preview error:", err);
  }
}

function _closeVoiceModal(voice) {
  // Stop any playing preview
  if (_previewSource) {
    try { _previewSource.stop(); } catch (_) {}
    _previewSource = null;
  }
  document.getElementById("voiceModal").classList.add("hidden");
  if (_voiceModalResolve) {
    _voiceModalResolve(voice);
    _voiceModalResolve = null;
  }
}

// Guard against null in case the HTML is served from a stale cache that
// doesn't yet contain the modal markup — prevents a TypeError that would
// otherwise block all subsequent event-listener registration in this file.
const _voiceConfirmBtn  = document.getElementById("voiceConfirmBtn");
const _voiceCancelBtn   = document.getElementById("voiceCancelBtn");
const _voiceModalEl     = document.getElementById("voiceModal");

if (_voiceConfirmBtn) {
  _voiceConfirmBtn.addEventListener("click", () => {
    const checked = document.querySelector("#voiceGrid input[type=radio]:checked");
    _closeVoiceModal(checked ? checked.value : "Aoede");
  });
}

if (_voiceCancelBtn) {
  _voiceCancelBtn.addEventListener("click", () => _closeVoiceModal(null));
}

if (_voiceModalEl) {
  // Close on overlay click (outside card)
  _voiceModalEl.addEventListener("click", (e) => {
    if (e.target === _voiceModalEl) _closeVoiceModal(null);
  });
}

// ── Source management ────────────────────────────────────────────────────────

async function loadSources() {
  if (!state.sessionId) return;
  try {
    const res = await apiFetch(`${API_BASE}/sessions/${state.sessionId}/sources`);
    if (!res.ok) return;
    const sources = await res.json();
    state.sessionSourceCount = sources.length;
    renderSources(sources);
  } catch (_) {}
}

function renderSources(sources) {
  state.currentSources = sources;
  els.sourceCount.textContent = `${sources.length}/10`;
  els.sourceList.innerHTML = "";
  showSourceError("");

  if (!sources.length) {
    els.sourceList.innerHTML = '<p class="muted-text">No sources yet.</p>';
    return;
  }

  for (const src of sources) {
    const row = document.createElement("div");
    row.className = "source-list-item";

    const icon = document.createElement("span");
    icon.className = "source-list-icon";
    icon.textContent = src.kind === "web_result" ? "🌐" : "📄";

    const name = document.createElement("span");
    name.className = "source-list-name";
    name.title = src.display_name;
    name.textContent = src.display_name;

    const status = document.createElement("span");
    status.className = "source-list-status";
    if (src.processing_status === "indexed") {
      status.textContent = "✓";
      status.style.color = "#16a34a";
    } else if (src.processing_status === "failed") {
      status.textContent = "✗";
      status.style.color = "#dc2626";
    } else {
      status.textContent = "…";
      status.style.color = "#9ca3af";
    }

    const del = document.createElement("button");
    del.className = "source-del-btn";
    del.title = "Delete";
    del.textContent = "✕";
    del.addEventListener("click", () => deleteSource(src.source_id));

    row.append(icon, name, status, del);
    els.sourceList.appendChild(row);
  }
}

async function deleteSource(sourceId) {
  if (!state.sessionId) return;
  const srcToDelete = state.currentSources.find(s => s.source_id === sourceId);
  try {
    const res = await apiFetch(`${API_BASE}/sessions/${state.sessionId}/sources/${sourceId}`, { method: "DELETE" });
    if (!res.ok) { const e = await res.json().catch(() => ({})); showSourceError(e.detail || "Delete failed"); return; }
    // Restore web results back to the search panel so they can be re-added
    if (srcToDelete && srcToDelete.kind === "web_result") {
      webState.results.unshift({
        title: srcToDelete.display_name || srcToDelete.original_filename || "",
        url: srcToDelete.source_url || "",
        snippet: "",
        checked: false,
      });
      renderWebResults();
    }
    await loadSources();
  } catch (e) { showSourceError(String(e)); }
}

async function uploadFile(file) {
  if (!state.sessionId) { showSourceError("Select or create a session first."); return; }
  if (state.sessionSourceCount >= 10) { showSourceError("Source limit reached (max 10)."); return; }

  showSourceError("");
  const textNode = els.uploadLabel.firstChild;
  const orig = textNode.textContent;
  textNode.textContent = " Uploading…";
  els.uploadLabel.style.opacity = "0.5";

  try {
    const form = new FormData();
    form.append("file", file);
    const res = await apiFetch(`${API_BASE}/sessions/${state.sessionId}/sources/upload`, { method: "POST", body: form });
    if (!res.ok) { const e = await res.json().catch(() => ({})); showSourceError(e.detail || "Upload failed"); return; }
    await loadSources();
  } catch (e) { showSourceError(String(e)); }
  finally { textNode.textContent = orig; els.uploadLabel.style.opacity = ""; els.fileInput.value = ""; }
}

// ── Web search ───────────────────────────────────────────────────────────────

function checkedResults() { return webState.results.filter(r => r.checked); }

function updateAddWebButton() {
  const n = checkedResults().length;
  els.addWebBtn.textContent = `Add selected (${n})`;
  els.addWebBtn.disabled = n === 0;
}

function renderWebResults() {
  els.webResultsList.innerHTML = "";
  webState.results.forEach((r, i) => {
    const item = document.createElement("div");
    item.className = "web-result-item" + (r.checked ? " checked" : "");

    const label = document.createElement("label");
    label.className = "web-result-label";

    const cb = document.createElement("input");
    cb.type = "checkbox";
    cb.checked = r.checked;
    cb.addEventListener("change", () => toggleWebResult(i, cb.checked));

    const txt = document.createElement("div");
    txt.className = "web-result-text";

    const title = document.createElement("div");
    title.className = "web-result-title";
    title.textContent = r.title || r.url;

    const snip = document.createElement("div");
    snip.className = "web-result-snippet";
    snip.textContent = r.snippet;

    txt.append(title, snip);
    label.append(cb, txt);
    item.appendChild(label);
    els.webResultsList.appendChild(item);
  });
  updateAddWebButton();
}

function toggleWebResult(index, checked) {
  if (index < 0 || index >= webState.results.length) return;

  // Unchecking always allowed
  if (!checked) {
    webState.results[index].checked = false;
    const items = els.webResultsList.querySelectorAll(".web-result-item");
    if (items[index]) items[index].classList.remove("checked");
    showWebError("");
    updateAddWebButton();
    return;
  }

  // Checking: validate cap
  const currentChecked = webState.results.filter(r => r.checked).length;
  if (currentChecked + state.sessionSourceCount >= 10) {
    const items = els.webResultsList.querySelectorAll(".web-result-item");
    if (items[index]) items[index].querySelector("input[type=checkbox]").checked = false;
    showWebError(`Cannot select more: ${state.sessionSourceCount} in session + ${currentChecked} checked = 10 max.`);
    return;
  }

  webState.results[index].checked = true;
  const items = els.webResultsList.querySelectorAll(".web-result-item");
  if (items[index]) items[index].classList.add("checked");
  showWebError("");
  updateAddWebButton();
}

async function performWebSearch() {
  if (!state.sessionId) { showWebError("Select or create a session first."); return; }
  const query = els.webSearchInput.value.trim();
  if (!query || webState.searching) return;

  webState.searching = true;
  showWebError("");
  els.webSearchBtn.disabled = true;
  els.webSearchBtn.textContent = "Searching…";

  try {
    const kept = checkedResults();
    if (state.sessionSourceCount + kept.length >= 10) {
      showWebError(`No capacity for new results.`);
      return;
    }

    const res = await apiFetch(`${API_BASE}/sessions/${state.sessionId}/sources/web-search`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ query, pending_count: kept.length }),
    });

    if (!res.ok) { const e = await res.json().catch(() => ({})); showWebError(e.detail || "Search failed"); return; }

    const data = await res.json();
    webState.results = [...kept, ...(data.results || []).map(r => ({ ...r, checked: false }))];
    renderWebResults();

  } catch (e) { showWebError(String(e)); }
  finally { webState.searching = false; els.webSearchBtn.disabled = false; els.webSearchBtn.textContent = "Search"; }
}

async function addSelectedWebSources() {
  if (!state.sessionId) return;
  const selected = checkedResults();
  if (!selected.length) return;

  if (state.sessionSourceCount + selected.length > 10) {
    showWebError(`Cannot add ${selected.length}: only ${10 - state.sessionSourceCount} slot(s) remaining.`);
    return;
  }

  els.addWebBtn.disabled = true;
  els.addWebBtn.textContent = "Adding…";
  showWebError("");

  try {
    const res = await apiFetch(`${API_BASE}/sessions/${state.sessionId}/sources/add-web`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ results: selected.map(({ title, url, snippet }) => ({ title, url, snippet })) }),
    });

    if (!res.ok) { const e = await res.json().catch(() => ({})); showWebError(e.detail || "Add failed"); els.addWebBtn.disabled = false; return; }

    webState.results = webState.results.filter(r => !r.checked);
    renderWebResults();
    await loadSources();
  } catch (e) { showWebError(String(e)); }
  finally { updateAddWebButton(); }
}

// ── Citations (source cues) ──────────────────────────────────────────────────

function renderCitations(evidence) {
  els.sourceCues.innerHTML = "";
  if (!evidence || !evidence.length) {
    els.sourceCues.innerHTML = '<p class="muted-text">Evidence appears after each turn.</p>';
    return;
  }
  for (const item of evidence) {
    const div = document.createElement("div");
    div.className = "citation-item";

    const name = document.createElement("div");
    name.className = "citation-name";
    name.textContent = item.source_name || "Unknown source";

    const meta = document.createElement("div");
    meta.className = "citation-meta";
    meta.textContent = [
      item.page    != null ? `p. ${item.page}`    : null,
      item.section          ? `§ ${item.section}` : null,
    ].filter(Boolean).join(" · ");

    const snip = document.createElement("div");
    snip.className = "citation-snippet";
    snip.textContent = item.text || "";

    div.append(name, meta, snip);
    els.sourceCues.appendChild(div);
  }
}

// ── Recap ────────────────────────────────────────────────────────────────────

function renderRecapPreview(recap) {
  els.recapPreview.innerHTML = "";
  if (!recap) {
    els.recapPreview.innerHTML = '<span class="recap-placeholder">Generate a note to recap this session.</span>';
    els.downloadRecapBtn.disabled = true;
    return;
  }

  if (recap.topic) {
    const t = document.createElement("div");
    t.className = "recap-topic";
    t.textContent = recap.topic;
    els.recapPreview.appendChild(t);
  }

  for (const [label, items] of [
    ["Key Insights", recap.key_insights],
    ["Sources Referenced", recap.sources_referenced],
    ["Open Questions", recap.open_questions],
    ["Next Steps", recap.next_steps],
  ]) {
    if (!items || !items.length) continue;
    const lbl = document.createElement("div");
    lbl.className = "recap-label";
    lbl.textContent = label;
    const ul = document.createElement("ul");
    ul.className = "recap-list";
    for (const item of items) {
      const li = document.createElement("li");
      li.textContent = item;
      ul.appendChild(li);
    }
    els.recapPreview.append(lbl, ul);
  }

  els.downloadRecapBtn.disabled = false;
}

async function generateRecap() {
  if (!state.sessionId) return;
  // Capture session before async work so we can guard against race conditions
  // where the user switches sessions while generation is in flight.
  const targetId = state.sessionId;
  els.generateRecapBtn.disabled = true;
  els.generateRecapBtn.textContent = "…";
  els.recapPreview.innerHTML = '<span class="recap-placeholder">Generating…</span>';

  try {
    const res = await apiFetch(`${API_BASE}/sessions/${targetId}/recap/generate`, { method: "POST" });
    if (!res.ok) { const e = await res.json().catch(() => ({})); els.recapPreview.innerHTML = `<span class="recap-placeholder" style="color:#dc2626">${e.detail || "Failed"}</span>`; return; }
    const recap = await res.json();
    // Only apply if the user hasn't switched to a different session
    if (state.sessionId === targetId) {
      state.recapData = recap;
      renderRecapPreview(recap);
    }
  } catch (e) {
    if (state.sessionId === targetId) {
      els.recapPreview.innerHTML = `<span class="recap-placeholder" style="color:#dc2626">${e}</span>`;
    }
  } finally {
    if (state.sessionId === targetId) {
      els.generateRecapBtn.disabled = false;
      els.generateRecapBtn.textContent = "Generate";
    }
  }
}

async function tryLoadExistingRecap() {
  if (!state.sessionId) return;
  // Capture the session we're loading for. If the user switches sessions while
  // the fetch is in flight we must not apply a stale result to the new session.
  const targetId = state.sessionId;
  state.recapData = null;
  renderRecapPreview(null);
  try {
    const res = await apiFetch(`${API_BASE}/sessions/${targetId}/recap`);
    if (res.ok && state.sessionId === targetId) {
      const r = await res.json();
      state.recapData = r;
      renderRecapPreview(r);
    }
  } catch (_) {}
}

function downloadRecap() {
  if (!state.recapData) return;
  const r = state.recapData;
  const lines = ["# Session Note\n"];
  if (r.topic) lines.push(`## Topic\n${r.topic}\n`);
  const sections = [["Key Insights", r.key_insights], ["Sources Referenced", r.sources_referenced], ["Open Questions", r.open_questions], ["Next Steps", r.next_steps]];
  for (const [label, items] of sections) {
    if (!items || !items.length) continue;
    lines.push(`## ${label}`);
    for (const item of items) lines.push(`- ${item}`);
    lines.push("");
  }
  if (r.generated_at) lines.push(`---\n*Generated: ${new Date(r.generated_at).toLocaleString()}*`);
  const blob = new Blob([lines.join("\n")], { type: "text/markdown;charset=utf-8" });
  const url  = URL.createObjectURL(blob);
  const a    = Object.assign(document.createElement("a"), { href: url, download: `note-${(state.sessionId || "session").slice(0, 8)}.md` });
  a.click();
  URL.revokeObjectURL(url);
}

// ── Follow-up suggestions ────────────────────────────────────────────────────

async function fetchFollowUpSuggestions() {
  if (!state.sessionId) return;
  try {
    const res = await apiFetch(`${API_BASE}/sessions/${state.sessionId}/recap/follow-up`, { method: "POST" });
    if (!res.ok) return;
    const data = await res.json();
    if (data.suggestions && data.suggestions.length) showFollowUpCard(data.suggestions);
  } catch (_) {}
}

function showFollowUpCard(suggestions) {
  const card = document.createElement("div");
  card.className = "followup-card";
  const title = document.createElement("div");
  title.className = "followup-card-title";
  title.textContent = "Suggested follow-ups";
  card.appendChild(title);
  const list = document.createElement("div");
  list.className = "followup-suggestions";
  for (const text of suggestions) {
    const chip = document.createElement("div");
    chip.className = "followup-chip";
    chip.textContent = text;
    list.appendChild(chip);
  }
  card.appendChild(list);
  els.messages.appendChild(card);
  scrollMessages();
}

// ── Session title ────────────────────────────────────────────────────────────

async function updateSessionTitle(title) {
  if (!state.sessionId) return;
  try {
    const res = await apiFetch(`${API_BASE}/sessions/${state.sessionId}`, {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ title }),
    });
    if (res.ok) loadSessionList();
  } catch (_) {}
}

// ── Audio helpers ────────────────────────────────────────────────────────────

function uint8ArrayToBase64(bytes) {
  const CHUNK = 0x8000;
  let binary = "";
  for (let offset = 0; offset < bytes.length; offset += CHUNK)
    binary += String.fromCharCode.apply(null, bytes.subarray(offset, Math.min(offset + CHUNK, bytes.length)));
  return btoa(binary);
}

function decodeBase64(b64) {
  const bin = atob(b64);
  const out = new Uint8Array(bin.length);
  for (let i = 0; i < bin.length; i++) out[i] = bin.charCodeAt(i);
  return out;
}

function pcm16ToFloat32(u8) {
  const v = new DataView(u8.buffer, u8.byteOffset, u8.byteLength);
  const f = new Float32Array(u8.byteLength / 2);
  for (let i = 0; i < f.length; i++) f[i] = v.getInt16(i * 2, true) / 32768;
  return f;
}

function floatTo16BitPCM(f32) {
  const buf  = new ArrayBuffer(f32.length * 2);
  const view = new DataView(buf);
  for (let i = 0; i < f32.length; i++) {
    const s = Math.max(-1, Math.min(1, f32[i]));
    view.setInt16(i * 2, s < 0 ? s * 0x8000 : s * 0x7fff, true);
  }
  return new Uint8Array(buf);
}

function downsample(buf, fromRate, toRate) {
  if (fromRate === toRate) return buf;
  const ratio  = fromRate / toRate;
  const newLen = Math.round(buf.length / ratio);
  const out    = new Float32Array(newLen);
  for (let i = 0; i < newLen; i++) {
    const start = Math.round(i * ratio);
    const end   = Math.round((i + 1) * ratio);
    let sum = 0, cnt = 0;
    for (let j = start; j < end && j < buf.length; j++) { sum += buf[j]; cnt++; }
    out[i] = cnt > 0 ? sum / cnt : 0;
  }
  return out;
}

async function ensureAudioContext() {
  if (!state.audioContext)
    state.audioContext = new (window.AudioContext || window.webkitAudioContext)();
  if (state.audioContext.state === "suspended")
    await state.audioContext.resume();
}

async function enqueueAssistantAudio(b64) {
  await ensureAudioContext();
  const f32 = pcm16ToFloat32(decodeBase64(b64));
  const buf = state.audioContext.createBuffer(1, f32.length, ASSISTANT_PCM_RATE);
  buf.copyToChannel(f32, 0);
  const src = state.audioContext.createBufferSource();
  src.buffer = buf;
  src.connect(state.audioContext.destination);
  const now = state.audioContext.currentTime;
  if (state.playbackTime < now) state.playbackTime = now;
  src.start(state.playbackTime);
  state.playbackTime += buf.duration;
  state.activeAudioNodes.push(src);
  src.onended = () => {
    const idx = state.activeAudioNodes.indexOf(src);
    if (idx > -1) state.activeAudioNodes.splice(idx, 1);
  };
}

function stopAssistantAudio() {
  for (const n of [...state.activeAudioNodes]) { try { n.stop(); } catch (_) {} }
  state.activeAudioNodes.length = 0;
  if (state.audioContext) state.playbackTime = state.audioContext.currentTime;
}

// ── Microphone ───────────────────────────────────────────────────────────────

// Energy gate: reduce silent chunks to prevent 1007, but still send keepalive
// audio every KEEPALIVE_MS to prevent 1006 (abnormal closure from inactivity).
const ENERGY_THRESHOLD        = 0.001;
const SPEECH_TAIL_MS          = 1500;
const KEEPALIVE_MS            = 3000;  // max silence gap before sending a keepalive chunk
const INTERRUPT_RMS_THRESHOLD = 0.015; // higher threshold for deliberate interrupt detection
const INTERRUPT_SUSTAIN_MS    = 50;    // ms of sustained speech above threshold to fire locally

function _makeSilentPcm(f32, sr) {
  return new Uint8Array(floatTo16BitPCM(new Float32Array(
    Math.round(f32.length * USER_PCM_RATE / sr)
  )));
}

function _sendAudioChunk(f32, sr) {
  if (!state.conversationActive || !state.runtimeReady) return;
  if (!state.ws || state.ws.readyState !== WebSocket.OPEN) return;

  // Compute RMS energy
  let sumSq = 0;
  for (let i = 0; i < f32.length; i++) sumSq += f32[i] * f32[i];
  const rms = Math.sqrt(sumSq / f32.length);

  const now = Date.now();

  // ── Agent is speaking: local interrupt detection with silent keepalive ──────
  // While the agent plays audio we suppress all real mic audio to prevent
  // ambient noise from triggering Gemini's VAD (START_SENSITIVITY_HIGH is very
  // sensitive).  We only send real audio once the user sustains speech above a
  // higher threshold for INTERRUPT_SUSTAIN_MS — at which point we also stop
  // the local audio immediately so the agent goes quiet without waiting for
  // the server round-trip.
  if (state.activeAudioNodes.length > 0 && !state._locallyInterrupted) {
    if (rms >= INTERRUPT_RMS_THRESHOLD) {
      if (state._interruptSpeechStart === 0) state._interruptSpeechStart = now;

      if (now - state._interruptSpeechStart >= INTERRUPT_SUSTAIN_MS) {
        // Sustained speech confirmed → immediate local interrupt
        state._locallyInterrupted = true;
        state._interruptSpeechStart = 0;
        stopAssistantAudio();
        // Fall through to send real audio and trigger Gemini VAD
      } else {
        // Not yet sustained — keep sending silent audio
        if (now - state._lastKeepaliveAt >= KEEPALIVE_MS) {
          state._lastKeepaliveAt = now;
          wsSend({ type: "audio_chunk", mime_type: "audio/pcm;rate=16000", data: uint8ArrayToBase64(_makeSilentPcm(f32, sr)) });
        }
        return;
      }
    } else {
      // Low-energy audio during agent speech — send silent keepalive only
      state._interruptSpeechStart = 0;
      if (now - state._lastKeepaliveAt >= KEEPALIVE_MS) {
        state._lastKeepaliveAt = now;
        wsSend({ type: "audio_chunk", mime_type: "audio/pcm;rate=16000", data: uint8ArrayToBase64(_makeSilentPcm(f32, sr)) });
      }
      return;
    }
  }

  // ── Normal speech gating (agent silent or interrupt already fired) ───────────
  if (rms >= ENERGY_THRESHOLD) {
    // Active speech — send immediately and reset timers
    state._lastSpeechAt = now;
    state._lastKeepaliveAt = now;
  } else if (now - state._lastSpeechAt > SPEECH_TAIL_MS) {
    // Silence period: only send a keepalive chunk every KEEPALIVE_MS.
    if (now - state._lastKeepaliveAt < KEEPALIVE_MS) {
      return; // skip — next keepalive not due yet
    }
    state._lastKeepaliveAt = now;
    // fall through and send one sparse keepalive chunk
  }

  const pcm = floatTo16BitPCM(downsample(f32, sr, USER_PCM_RATE));
  wsSend({ type: "audio_chunk", mime_type: "audio/pcm;rate=16000", data: uint8ArrayToBase64(pcm) });
}

async function startMic() {
  await ensureAudioContext();
  const stream = await navigator.mediaDevices.getUserMedia({
    audio: { channelCount: 1, echoCancellation: true, noiseSuppression: true, autoGainControl: true },
  });
  state.mediaStream = stream;
  const source = state.audioContext.createMediaStreamSource(stream);
  const sr = state.audioContext.sampleRate;
  let usedWorklet = false;

  if (window.AudioWorkletNode) {
    try {
      await state.audioContext.audioWorklet.addModule("/static/mic-processor.js");
      const worklet = new AudioWorkletNode(state.audioContext, "mic-processor");
      worklet.port.onmessage = (ev) => _sendAudioChunk(new Float32Array(ev.data), sr);
      const silentGain = state.audioContext.createGain();
      silentGain.gain.value = 0;
      source.connect(worklet);
      worklet.connect(silentGain);
      silentGain.connect(state.audioContext.destination);
      state.mediaSourceNode = source;
      state.workletNode = worklet;
      usedWorklet = true;
    } catch (e) { console.warn("AudioWorklet fallback:", e); }
  }

  if (!usedWorklet) {
    const proc = state.audioContext.createScriptProcessor(4096, 1, 1);
    proc.onaudioprocess = (ev) => _sendAudioChunk(ev.inputBuffer.getChannelData(0).slice(), sr);
    const silentGain = state.audioContext.createGain();
    silentGain.gain.value = 0;
    source.connect(proc);
    proc.connect(silentGain);
    silentGain.connect(state.audioContext.destination);
    state.mediaSourceNode = source;
    state.processorNode = proc;
  }

  setMicActive(true);
}

function stopMic() {
  if (state.workletNode) { state.workletNode.port.onmessage = null; state.workletNode.disconnect(); state.workletNode = null; }
  if (state.processorNode) { state.processorNode.onaudioprocess = null; state.processorNode.disconnect(); state.processorNode = null; }
  if (state.mediaSourceNode) { state.mediaSourceNode.disconnect(); state.mediaSourceNode = null; }
  if (state.mediaStream) { state.mediaStream.getTracks().forEach(t => t.stop()); state.mediaStream = null; }
  setMicActive(false);
}

// ── WebSocket ────────────────────────────────────────────────────────────────

function openWebSocket(sessionId) {
  return new Promise((resolve, reject) => {
    const ws = new WebSocket(`${WS_BASE}/ws/live/${sessionId}?client_id=${CLIENT_ID}`);
    state.ws = ws;
    let settled = false;
    const settle = (fn, val) => { if (settled) return; settled = true; clearTimeout(timer); fn(val); };
    const timer = setTimeout(() => settle(reject, new Error("WebSocket connection timed out")), 15_000);

    ws.onmessage = (ev) => {
      const p = JSON.parse(ev.data);
      handleServerEvent(p);
      if (p.type === "connected") settle(resolve);
    };
    ws.onerror = () => settle(reject, new Error("WebSocket error"));
    ws.onclose = (ev) => {
      settle(reject, new Error(`WebSocket closed (${ev.code}) before connecting`));
      state.runtimeReady = false;
      if (state.conversationActive) {
        setStatus("Connection lost — please restart.");
        addSystemMessage("Connection lost.");
        state.conversationActive = false;
        stopMic();
        updateButtons();
      }
    };
  });
}

function wsSend(obj) {
  if (state.ws && state.ws.readyState === WebSocket.OPEN) state.ws.send(JSON.stringify(obj));
}

// ── Server event handler ─────────────────────────────────────────────────────

function handleServerEvent(payload) {
  switch (payload.type) {
    case "connected":
      setStatus(`Connected`);
      break;
    case "runtime_connecting":
      setStatus("Starting Gemini Live…");
      break;
    case "runtime_ready":
      state.runtimeReady = true;
      setStatus("Listening — speak to start");
      break;

    case "user_transcript": {
      const text = (payload.text || "").trim();
      if (!text) break;
      // If we're in the 350 ms pending window after turn_complete, update
      // the already-finalised bubble with the late-arriving ASR text
      if (state.pendingUserBubble) {
        state.pendingUserBubble.textContent = text;
        state.pendingUserBubble.dataset.pending = "false";
        break;
      }
      state.currentUserText = text;
      if (!state.liveUserBubble) state.liveUserBubble = createLiveBubble("user");
      state.liveUserBubble.textContent = `🎤 ${text}`;
      scrollMessages();
      break;
    }

    case "assistant_transcript": {
      // output_audio_transcription arrives word-by-word (incremental) — append
      const text = (payload.text || "").trim();
      if (!text) break;
      state.currentAssistantText += (state.currentAssistantText ? " " : "") + text;
      if (!state.liveAssistantBubble) state.liveAssistantBubble = createLiveBubble("assistant");
      state.liveAssistantBubble.textContent = `🔊 ${state.currentAssistantText}`;
      scrollMessages();
      break;
    }

    case "assistant_text":
      // model_turn text parts overlap with output_audio_transcription — ignore
      // to avoid duplicating the same speech in the transcript bubble
      break;

    case "assistant_audio_chunk":
      enqueueAssistantAudio(payload.data);
      if (!state.liveAssistantBubble) state.liveAssistantBubble = createLiveBubble("assistant");
      break;

    case "assistant_interrupted":
      stopAssistantAudio();
      // Clear local interrupt flags — server has confirmed the interruption
      state._locallyInterrupted = false;
      state._interruptSpeechStart = 0;
      if (state.liveAssistantBubble) {
        finaliseBubble(state.liveAssistantBubble, state.currentAssistantText.trim() ? `${state.currentAssistantText.trim()} ✂` : null);
        state.liveAssistantBubble = null;
        state.currentAssistantText = "";
      }
      // Flush any pending user bubble immediately (don't wait for the 350 ms timer)
      if (state.pendingUserBubble) {
        clearTimeout(state._pendingUserBubbleTimer);
        finaliseBubble(state.pendingUserBubble, state.pendingUserBubble.textContent.replace(/^🎤\s*/, "") || "");
        state.pendingUserBubble = null;
        state._pendingUserBubbleTimer = null;
      }
      setStatus("Interrupted — listening…");
      break;

    case "turn_complete": {
      const userText = state.currentUserText;

      // Clear local interrupt flags — turn is fully complete
      state._locallyInterrupted = false;
      state._interruptSpeechStart = 0;

      // Finalise assistant bubble immediately
      finaliseBubble(state.liveAssistantBubble, state.currentAssistantText || "(audio response)");
      state.liveAssistantBubble = null; state.currentAssistantText = "";

      // Delay user-bubble finalisation by 350 ms to catch any late-arriving
      // final ASR transcript from Gemini (cumulative text after turn end)
      const bubbleToHold = state.liveUserBubble;
      state.liveUserBubble = null;
      state.currentUserText = "";

      if (bubbleToHold) {
        // Promote to pending so user_transcript events can still update it
        state.pendingUserBubble = bubbleToHold;
        clearTimeout(state._pendingUserBubbleTimer);
        state._pendingUserBubbleTimer = setTimeout(() => {
          finaliseBubble(state.pendingUserBubble, state.pendingUserBubble.textContent.replace(/^🎤\s*/, "") || userText);
          state.pendingUserBubble = null;
          state._pendingUserBubbleTimer = null;
        }, 350);
      } else {
        finaliseBubble(null, userText);
      }

      if (state.conversationActive) setStatus("Listening…");
      // Auto-title the session from the first user utterance
      if (!state.titleSet && userText) {
        state.titleSet = true;
        const title = userText.length > 45 ? userText.slice(0, 45).trimEnd() + "…" : userText;
        updateSessionTitle(title);
      }
      break;
    }

    case "source_cues":
      renderCitations(payload.evidence || []);
      break;

    case "runtime_closed":
      state.runtimeReady = false;
      if (state.conversationActive) {
        state.startupFailed = true;
        setStatus("Session closed. Please end and restart.");
        addSystemMessage("Session disconnected.");
        state.conversationActive = false;
        stopMic();
        updateButtons();
      }
      break;

    case "runtime_error":
    case "error":
      setStatus(`Error: ${payload.message}`);
      addSystemMessage(`Error: ${payload.message}`);
      if (state.conversationActive && !state.runtimeReady) state.startupFailed = true;
      break;

    default:
      console.debug("Unhandled:", payload);
  }
}

// ── Conversation flow ────────────────────────────────────────────────────────

async function beginConversation() {
  if (!state.sessionId) throw new Error("Select or create a session first.");

  state.conversationActive = true;
  state.startupFailed = false;
  updateButtons();
  loadSources();

  if (!state.ws || state.ws.readyState !== WebSocket.OPEN) {
    setStatus("Connecting…");
    els.beginBtn.textContent = "Connecting…";
    await openWebSocket(state.sessionId);
  }

  setStatus("Starting Gemini Live…");
  els.beginBtn.textContent = "Starting…";
  wsSend({ type: "begin_conversation" });

  await waitFor(() => state.runtimeReady || state.startupFailed, 90_000, "Gemini Live startup timed out");
  if (!state.runtimeReady) throw new Error("Gemini Live failed to start. Check credentials.");
  els.beginBtn.textContent = "▶ Begin";

  await startMic();
  setStatus("Listening — speak naturally");
  addSystemMessage("Conversation started. Speak to begin.");
}

async function endConversation() {
  state.conversationActive = false;
  stopMic();
  stopAssistantAudio();

  // Flush pending user bubble timer immediately
  if (state.pendingUserBubble) {
    clearTimeout(state._pendingUserBubbleTimer);
    finaliseBubble(state.pendingUserBubble, state.pendingUserBubble.textContent.replace(/^🎤\s*/, "") || "");
    state.pendingUserBubble = null;
    state._pendingUserBubbleTimer = null;
  }

  finaliseBubble(state.liveUserBubble, state.currentUserText);
  state.liveUserBubble = null; state.currentUserText = "";
  finaliseBubble(state.liveAssistantBubble, state.currentAssistantText);
  state.liveAssistantBubble = null; state.currentAssistantText = "";

  if (state.ws && state.ws.readyState === WebSocket.OPEN) {
    wsSend({ type: "end_conversation" });
    state.ws.close();
  }

  state.runtimeReady = false;
  updateButtons();
  setStatus("Conversation ended");
  addSystemMessage("Conversation ended.");

  fetchFollowUpSuggestions();
  loadSessionList();  // refresh to show updated message count
}

// ── Utility ──────────────────────────────────────────────────────────────────

function waitFor(cond, timeout = 10_000, msg = "Timeout") {
  return new Promise((resolve, reject) => {
    const start = Date.now();
    const id = setInterval(() => {
      if (cond()) { clearInterval(id); resolve(); }
      else if (Date.now() - start > timeout) { clearInterval(id); reject(new Error(msg)); }
    }, 100);
  });
}

// ── Event listeners ──────────────────────────────────────────────────────────

els.newChatBtn.addEventListener("click", async () => {
  if (state.conversationActive) {
    if (!confirm("End current conversation and start new?")) return;
    await endConversation();
  }

  // Show voice selection modal; null means user cancelled
  const selectedVoice = await showVoiceModal();
  if (selectedVoice === null) return;

  try {
    const data = await createSession("New conversation", selectedVoice);
    state.sessionId = data.session_id;
    state.sessionSourceCount = 0;
    state.recapData = null;
    state.titleSet = false; // allow auto-titling from first utterance

    els.messages.innerHTML = "";
    renderSources([]);
    renderRecapPreview(null);
    webState.results = [];
    renderWebResults();
    setStatus("New session ready — click Begin Conversation");
    updateButtons();

    addSystemMessage(`Session created: ${data.session_id}`);
    await loadSessionList();

    // Highlight the new session
    document.querySelectorAll(".session-item").forEach(el => {
      el.classList.toggle("active", el.dataset.sessionId === data.session_id);
    });
  } catch (e) {
    setStatus(`Error: ${e.message}`);
  }
});

els.beginBtn.addEventListener("click", async () => {
  try {
    await beginConversation();
  } catch (e) {
    state.conversationActive = false;
    els.beginBtn.textContent = "▶ Begin";
    updateButtons();
    setStatus(String(e));
    addSystemMessage(`Error: ${e}`);
    console.error(e);
  }
});

els.endBtn.addEventListener("click", () => {
  endConversation().catch(e => { setStatus(String(e)); console.error(e); });
});

els.fileInput.addEventListener("change", () => {
  const f = els.fileInput.files[0];
  if (f) uploadFile(f);
});

els.webSearchBtn.addEventListener("click", performWebSearch);
els.webSearchInput.addEventListener("keydown", e => { if (e.key === "Enter") performWebSearch(); });
els.addWebBtn.addEventListener("click", addSelectedWebSources);

els.generateRecapBtn.addEventListener("click", generateRecap);
els.downloadRecapBtn.addEventListener("click", downloadRecap);

// ── Init ─────────────────────────────────────────────────────────────────────

updateButtons();
loadSessionList();
