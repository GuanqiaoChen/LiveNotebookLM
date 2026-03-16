/**
 * LiveNotebookLM – production-grade frontend for the Gemini Live voice agent.
 *
 * Audio pipeline:
 *   Mic  →  AudioWorkletNode (audio thread)  →  postMessage  →  main thread
 *        →  downsample to 16 kHz  →  PCM-16  →  base64  →  WebSocket
 *
 * Using AudioWorklet keeps the audio capture entirely off the main JS thread,
 * so UI operations (fetch, DOM updates, web search) are never blocked.
 *
 * Playback:
 *   WebSocket  →  base64 decode  →  PCM-16  →  Float32  →  AudioBufferSource
 *
 * VAD interrupt is handled automatically by Gemini Live server-side.
 */

const API_BASE = window.location.origin;
const WS_BASE  = window.location.origin.replace(/^http/, "ws");

const ASSISTANT_PCM_RATE = 24000;
const USER_PCM_RATE      = 16000;

// ── State ──────────────────────────────────────────────────────────────────

const state = {
  ws: null,
  sessionId: "",
  runtimeReady: false,
  conversationActive: false,
  startupFailed: false,
  sessionSourceCount: 0,

  // Web Audio
  audioContext: null,
  playbackTime: 0,
  activeAudioNodes: [],

  // Microphone pipeline (AudioWorklet primary, ScriptProcessor fallback)
  mediaStream: null,
  mediaSourceNode: null,
  workletNode: null,       // AudioWorkletNode (preferred)
  processorNode: null,     // ScriptProcessorNode (fallback)

  // Live transcript bubbles
  liveUserBubble: null,
  liveAssistantBubble: null,

  currentUserText: "",
  currentAssistantText: "",
};

// ── Web search state ────────────────────────────────────────────────────────

const webState = {
  results: [],    // [{ title, url, snippet, checked }]
  searching: false,
};

// ── DOM refs ────────────────────────────────────────────────────────────────

const els = {
  sessionTitle:    document.getElementById("sessionTitle"),
  sessionId:       document.getElementById("sessionId"),
  createBtn:       document.getElementById("createSessionBtn"),
  beginBtn:        document.getElementById("beginConvBtn"),
  endBtn:          document.getElementById("endConvBtn"),
  statusBox:       document.getElementById("statusBox"),
  micIndicator:    document.getElementById("micIndicator"),
  messages:        document.getElementById("messages"),
  sourceCues:      document.getElementById("sourceCues"),
  // Sources panel
  sourceList:      document.getElementById("sourceList"),
  sourceCount:     document.getElementById("sourceCount"),
  sourceListError: document.getElementById("sourceListError"),
  fileInput:       document.getElementById("fileUploadInput"),
  uploadLabel:     document.getElementById("uploadLabel"),
  // Web search
  webSearchInput:  document.getElementById("webSearchInput"),
  webSearchBtn:    document.getElementById("webSearchBtn"),
  webResultsList:  document.getElementById("webResultsList"),
  addWebBtn:       document.getElementById("addWebBtn"),
  webSearchError:  document.getElementById("webSearchError"),
};

// ── UI helpers ───────────────────────────────────────────────────────────────

function setStatus(text) {
  els.statusBox.textContent = text;
}

function setMicActive(on) {
  if (!els.micIndicator) return;
  els.micIndicator.className = "mic-dot " + (on ? "mic-on" : "mic-off");
  els.micIndicator.title = on ? "Mic active" : "Mic off";
}

function updateButtons() {
  const active = state.conversationActive;
  els.beginBtn.disabled = active;
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

function createLiveBubble(role, placeholder) {
  const div = document.createElement("div");
  div.className = `msg ${role} bubble-live`;
  div.textContent = placeholder || "…";
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

function renderSourceCues(evidence) {
  els.sourceCues.innerHTML = "";
  if (!evidence || !evidence.length) {
    const empty = document.createElement("div");
    empty.className = "muted";
    empty.textContent = "No source cues for this turn";
    els.sourceCues.appendChild(empty);
    return;
  }
  for (const item of evidence) {
    const div = document.createElement("div");
    div.className = "source-cue-item";

    const head = document.createElement("div");
    head.className = "source-head";
    head.textContent = item.source_name || "unknown source";
    div.appendChild(head);

    const meta = document.createElement("div");
    meta.className = "source-meta";
    meta.textContent = [
      item.page    != null ? `p. ${item.page}` : null,
      item.section          ? `§ ${item.section}` : null,
      item.score   != null  ? `score ${Number(item.score).toFixed(2)}` : null,
    ].filter(Boolean).join(" · ");
    div.appendChild(meta);

    const snippet = document.createElement("div");
    snippet.className = "source-snippet";
    snippet.textContent = item.text || "";
    div.appendChild(snippet);

    els.sourceCues.appendChild(div);
  }
}

// ── Source management ────────────────────────────────────────────────────────

async function loadSources() {
  const sessionId = state.sessionId || els.sessionId.value.trim();
  if (!sessionId) return;
  try {
    const res = await fetch(`${API_BASE}/sessions/${sessionId}/sources`);
    if (!res.ok) return;
    const sources = await res.json();
    state.sessionSourceCount = sources.length;
    renderSources(sources);
  } catch (_) {}
}

function renderSources(sources) {
  els.sourceCount.textContent = `${sources.length}/10`;
  els.sourceList.innerHTML = "";
  showSourceError("");

  if (!sources.length) {
    const empty = document.createElement("div");
    empty.className = "muted";
    empty.textContent = "No sources yet.";
    els.sourceList.appendChild(empty);
    return;
  }

  for (const src of sources) {
    const row = document.createElement("div");
    row.className = "source-list-item";

    const icon = document.createElement("span");
    icon.className = "source-list-icon";
    icon.textContent = src.kind === "web_result" ? "🌐" : "📄";
    row.appendChild(icon);

    const name = document.createElement("span");
    name.className = "source-list-name";
    name.title = src.display_name;
    name.textContent = src.display_name;
    row.appendChild(name);

    const status = document.createElement("span");
    status.className = "source-list-status";
    if (src.processing_status === "failed") {
      status.textContent = "✗";
      status.style.color = "#ef4444";
    } else if (src.processing_status === "indexed") {
      status.textContent = "✓";
      status.style.color = "#22c55e";
    } else {
      status.textContent = "…";
    }
    row.appendChild(status);

    const delBtn = document.createElement("button");
    delBtn.className = "source-del-btn";
    delBtn.title = "Delete source";
    delBtn.textContent = "✕";
    delBtn.addEventListener("click", () => deleteSource(src.source_id));
    row.appendChild(delBtn);

    els.sourceList.appendChild(row);
  }
}

async function deleteSource(sourceId) {
  const sessionId = state.sessionId || els.sessionId.value.trim();
  if (!sessionId) return;
  try {
    const res = await fetch(
      `${API_BASE}/sessions/${sessionId}/sources/${sourceId}`,
      { method: "DELETE" }
    );
    if (!res.ok) {
      const err = await res.json().catch(() => ({}));
      showSourceError(err.detail || `Delete failed (${res.status})`);
      return;
    }
    await loadSources();
  } catch (err) {
    showSourceError(String(err));
  }
}

async function uploadFile(file) {
  const sessionId = state.sessionId || els.sessionId.value.trim();
  if (!sessionId) {
    showSourceError("Create or enter a session ID first.");
    return;
  }
  if (state.sessionSourceCount >= 10) {
    showSourceError("Source limit reached (max 10 per session).");
    return;
  }

  showSourceError("");
  // Update label text without touching the hidden <input>
  const textNode = els.uploadLabel.firstChild;
  const origText = textNode.textContent;
  textNode.textContent = " Uploading…";
  els.uploadLabel.style.opacity = "0.6";

  try {
    const form = new FormData();
    form.append("file", file);
    const res = await fetch(
      `${API_BASE}/sessions/${sessionId}/sources/upload`,
      { method: "POST", body: form }
    );
    if (!res.ok) {
      const err = await res.json().catch(() => ({}));
      showSourceError(err.detail || `Upload failed (${res.status})`);
      return;
    }
    await loadSources();
  } catch (err) {
    showSourceError(String(err));
  } finally {
    textNode.textContent = origText;
    els.uploadLabel.style.opacity = "";
    els.fileInput.value = "";
  }
}

// ── Web search ───────────────────────────────────────────────────────────────

function checkedResults() {
  return webState.results.filter(r => r.checked);
}

function updateAddWebButton() {
  const n = checkedResults().length;
  els.addWebBtn.textContent = `Add selected (${n})`;
  els.addWebBtn.disabled = n === 0;
}

function renderWebResults() {
  els.webResultsList.innerHTML = "";
  if (!webState.results.length) return;

  webState.results.forEach((r, i) => {
    const item = document.createElement("div");
    item.className = "web-result-item" + (r.checked ? " checked" : "");

    const label = document.createElement("label");
    label.className = "web-result-label";

    const cb = document.createElement("input");
    cb.type = "checkbox";
    cb.checked = r.checked;
    cb.addEventListener("change", () => toggleWebResult(i, cb.checked));

    const textDiv = document.createElement("div");
    textDiv.className = "web-result-text";

    const title = document.createElement("div");
    title.className = "web-result-title";
    title.textContent = r.title || r.url;

    const snippet = document.createElement("div");
    snippet.className = "web-result-snippet";
    snippet.textContent = r.snippet;

    textDiv.appendChild(title);
    textDiv.appendChild(snippet);
    label.appendChild(cb);
    label.appendChild(textDiv);
    item.appendChild(label);
    els.webResultsList.appendChild(item);
  });

  updateAddWebButton();
}

function toggleWebResult(index, checked) {
  if (index < 0 || index >= webState.results.length) return;

  const totalAfter = (checkedResults().length - (webState.results[index].checked ? 1 : 0))
                     + (checked ? 1 : 0)
                     + state.sessionSourceCount;

  if (checked && totalAfter > 10) {
    const items = els.webResultsList.querySelectorAll(".web-result-item");
    if (items[index]) {
      items[index].querySelector("input[type=checkbox]").checked = false;
    }
    showWebError(
      `Cannot select more: session has ${state.sessionSourceCount} source(s) ` +
      `and ${checkedResults().length} checked (max 10 total).`
    );
    return;
  }

  webState.results[index].checked = checked;
  const items = els.webResultsList.querySelectorAll(".web-result-item");
  if (items[index]) items[index].classList.toggle("checked", checked);
  showWebError("");
  updateAddWebButton();
}

async function performWebSearch() {
  const sessionId = state.sessionId || els.sessionId.value.trim();
  if (!sessionId) { showWebError("Create or enter a session ID first."); return; }

  const query = els.webSearchInput.value.trim();
  if (!query) return;
  if (webState.searching) return;

  webState.searching = true;
  showWebError("");
  els.webSearchBtn.disabled = true;
  els.webSearchBtn.textContent = "…";

  try {
    const kept = checkedResults();
    const pendingCount = kept.length;

    if (state.sessionSourceCount + pendingCount >= 10) {
      showWebError(
        `No capacity: session has ${state.sessionSourceCount} source(s) ` +
        `and ${pendingCount} are checked to add.`
      );
      return;
    }

    const res = await fetch(`${API_BASE}/sessions/${sessionId}/sources/web-search`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ query, pending_count: pendingCount }),
    });

    if (!res.ok) {
      const err = await res.json().catch(() => ({}));
      showWebError(err.detail || `Search failed (${res.status})`);
      return;
    }

    const data = await res.json();
    const newResults = (data.results || []).map(r => ({ ...r, checked: false }));
    webState.results = [...kept, ...newResults];
    renderWebResults();

  } catch (err) {
    showWebError(String(err));
  } finally {
    webState.searching = false;
    els.webSearchBtn.disabled = false;
    els.webSearchBtn.textContent = "🔍";
  }
}

async function addSelectedWebSources() {
  const sessionId = state.sessionId || els.sessionId.value.trim();
  if (!sessionId) return;

  const selected = checkedResults();
  if (!selected.length) return;

  if (state.sessionSourceCount + selected.length > 10) {
    showWebError(
      `Cannot add ${selected.length}: only ${10 - state.sessionSourceCount} slot(s) remaining.`
    );
    return;
  }

  els.addWebBtn.disabled = true;
  showWebError("");

  try {
    const res = await fetch(`${API_BASE}/sessions/${sessionId}/sources/add-web`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        results: selected.map(({ title, url, snippet }) => ({ title, url, snippet })),
      }),
    });

    if (!res.ok) {
      const err = await res.json().catch(() => ({}));
      showWebError(err.detail || `Add failed (${res.status})`);
      els.addWebBtn.disabled = false;
      return;
    }

    webState.results = webState.results.filter(r => !r.checked);
    renderWebResults();
    await loadSources();

  } catch (err) {
    showWebError(String(err));
    els.addWebBtn.disabled = false;
  }
}

// ── Audio encoding helpers ────────────────────────────────────────────────────

/**
 * Convert Uint8Array → base64 without using spread (spread causes stack
 * overflows on large arrays and is ~10× slower).
 */
function uint8ArrayToBase64(bytes) {
  const CHUNK = 0x8000;  // 32 KB chunks
  let binary = "";
  for (let offset = 0; offset < bytes.length; offset += CHUNK) {
    binary += String.fromCharCode.apply(
      null, bytes.subarray(offset, Math.min(offset + CHUNK, bytes.length))
    );
  }
  return btoa(binary);
}

function decodeBase64(b64) {
  const bin   = atob(b64);
  const bytes = new Uint8Array(bin.length);
  for (let i = 0; i < bin.length; i++) bytes[i] = bin.charCodeAt(i);
  return bytes;
}

function pcm16ToFloat32(uint8) {
  const view = new DataView(uint8.buffer, uint8.byteOffset, uint8.byteLength);
  const f32  = new Float32Array(uint8.byteLength / 2);
  for (let i = 0; i < f32.length; i++) f32[i] = view.getInt16(i * 2, true) / 32768;
  return f32;
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

function downsample(buffer, fromRate, toRate) {
  if (fromRate === toRate) return buffer;
  const ratio  = fromRate / toRate;
  const newLen = Math.round(buffer.length / ratio);
  const result = new Float32Array(newLen);
  for (let i = 0; i < newLen; i++) {
    const start = Math.round(i * ratio);
    const end   = Math.round((i + 1) * ratio);
    let accum = 0, count = 0;
    for (let j = start; j < end && j < buffer.length; j++) { accum += buffer[j]; count++; }
    result[i] = count > 0 ? accum / count : 0;
  }
  return result;
}

// ── Playback ──────────────────────────────────────────────────────────────────

async function ensureAudioContext() {
  if (!state.audioContext) {
    state.audioContext = new (window.AudioContext || window.webkitAudioContext)();
  }
  if (state.audioContext.state === "suspended") {
    await state.audioContext.resume();
  }
}

async function enqueueAssistantAudio(b64) {
  await ensureAudioContext();

  const bytes = decodeBase64(b64);
  const f32   = pcm16ToFloat32(bytes);
  const buf   = state.audioContext.createBuffer(1, f32.length, ASSISTANT_PCM_RATE);
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
  for (const node of [...state.activeAudioNodes]) {
    try { node.stop(); } catch (_) {}
  }
  state.activeAudioNodes.length = 0;
  if (state.audioContext) state.playbackTime = state.audioContext.currentTime;
}

// ── Microphone capture ────────────────────────────────────────────────────────

/**
 * Shared processing callback – called from either the AudioWorklet message
 * handler or the ScriptProcessor onaudioprocess handler.
 * Downsamples, converts to PCM-16, and sends via WebSocket.
 */
function _sendAudioChunk(f32, nativeSampleRate) {
  if (!state.conversationActive || !state.runtimeReady) return;
  if (!state.ws || state.ws.readyState !== WebSocket.OPEN) return;

  const downsampled = downsample(f32, nativeSampleRate, USER_PCM_RATE);
  const pcm = floatTo16BitPCM(downsampled);

  wsSend({
    type:      "audio_chunk",
    mime_type: "audio/pcm;rate=16000",
    data:      uint8ArrayToBase64(pcm),
  });
}

async function startMic() {
  await ensureAudioContext();

  const stream = await navigator.mediaDevices.getUserMedia({
    audio: {
      channelCount:     1,
      echoCancellation: true,
      noiseSuppression: true,
      autoGainControl:  true,
    },
  });

  state.mediaStream = stream;
  const source = state.audioContext.createMediaStreamSource(stream);
  const sr = state.audioContext.sampleRate;

  // ── Primary: AudioWorklet (audio thread, zero main-thread blocking) ─────────
  let usedWorklet = false;
  if (window.AudioWorkletNode) {
    try {
      await state.audioContext.audioWorklet.addModule("/static/mic-processor.js");
      const workletNode = new AudioWorkletNode(state.audioContext, "mic-processor");

      workletNode.port.onmessage = (ev) => {
        _sendAudioChunk(new Float32Array(ev.data), sr);
      };

      // Route through a silent gain so the audio graph stays active without
      // feeding mic audio to speakers (which would cause echo).
      const silentGain = state.audioContext.createGain();
      silentGain.gain.value = 0;
      source.connect(workletNode);
      workletNode.connect(silentGain);
      silentGain.connect(state.audioContext.destination);

      state.mediaSourceNode = source;
      state.workletNode     = workletNode;
      usedWorklet = true;

    } catch (err) {
      console.warn("AudioWorklet unavailable, falling back to ScriptProcessor:", err);
    }
  }

  // ── Fallback: ScriptProcessor (main thread) ──────────────────────────────
  if (!usedWorklet) {
    const processor = state.audioContext.createScriptProcessor(4096, 1, 1);

    processor.onaudioprocess = (ev) => {
      // slice() copies the buffer so it remains valid after the event
      _sendAudioChunk(ev.inputBuffer.getChannelData(0).slice(), sr);
    };

    const silentGain = state.audioContext.createGain();
    silentGain.gain.value = 0;
    source.connect(processor);
    processor.connect(silentGain);
    silentGain.connect(state.audioContext.destination);

    state.mediaSourceNode = source;
    state.processorNode   = processor;
  }

  setMicActive(true);
}

function stopMic() {
  if (state.workletNode) {
    state.workletNode.port.onmessage = null;
    state.workletNode.disconnect();
    state.workletNode = null;
  }
  if (state.processorNode) {
    state.processorNode.onaudioprocess = null;
    state.processorNode.disconnect();
    state.processorNode = null;
  }
  if (state.mediaSourceNode) {
    state.mediaSourceNode.disconnect();
    state.mediaSourceNode = null;
  }
  if (state.mediaStream) {
    state.mediaStream.getTracks().forEach(t => t.stop());
    state.mediaStream = null;
  }
  setMicActive(false);
}

// ── WebSocket ────────────────────────────────────────────────────────────────

function openWebSocket(sessionId) {
  return new Promise((resolve, reject) => {
    const ws = new WebSocket(`${WS_BASE}/ws/live/${sessionId}`);
    state.ws = ws;

    let settled = false;
    const settle = (fn, val) => {
      if (settled) return;
      settled = true;
      clearTimeout(timer);
      fn(val);
    };

    const timer = setTimeout(
      () => settle(reject, new Error("WebSocket connection timed out")), 15_000
    );

    ws.onmessage = (ev) => {
      const payload = JSON.parse(ev.data);
      handleServerEvent(payload);
      if (payload.type === "connected") settle(resolve);
    };

    ws.onerror = () => settle(reject, new Error("WebSocket error"));

    ws.onclose = (event) => {
      settle(reject, new Error(`WebSocket closed (code ${event.code}) before connecting`));
      state.runtimeReady = false;
      if (state.conversationActive) {
        setStatus("Connection lost — please end and restart the conversation.");
        addSystemMessage("Connection lost.");
        state.conversationActive = false;
        stopMic();
        updateButtons();
      } else {
        setStatus("Disconnected");
      }
    };
  });
}

function wsSend(obj) {
  if (state.ws && state.ws.readyState === WebSocket.OPEN) {
    state.ws.send(JSON.stringify(obj));
  }
}

// ── Server event handler ──────────────────────────────────────────────────────

function handleServerEvent(payload) {
  switch (payload.type) {

    case "connected":
      setStatus(`Connected (session ${payload.session_id})`);
      break;

    case "runtime_connecting":
      setStatus("Starting Gemini Live…");
      break;

    case "runtime_ready":
      state.runtimeReady = true;
      setStatus("Listening — speak to start the conversation");
      break;

    case "user_transcript": {
      const text = (payload.text || "").trim();
      if (!text) break;
      state.currentUserText = text;
      if (!state.liveUserBubble) state.liveUserBubble = createLiveBubble("user", "");
      state.liveUserBubble.textContent = `🎤 ${text}`;
      scrollMessages();
      break;
    }

    case "assistant_transcript":
    case "assistant_text": {
      const text = (payload.text || "").trim();
      if (!text) break;
      state.currentAssistantText += (state.currentAssistantText ? " " : "") + text;
      if (!state.liveAssistantBubble) state.liveAssistantBubble = createLiveBubble("assistant", "");
      state.liveAssistantBubble.textContent = `🔊 ${state.currentAssistantText}`;
      scrollMessages();
      break;
    }

    case "assistant_audio_chunk":
      enqueueAssistantAudio(payload.data);
      if (!state.liveAssistantBubble) {
        state.liveAssistantBubble = createLiveBubble("assistant", "🔊 …");
      }
      break;

    case "assistant_interrupted":
      stopAssistantAudio();
      if (state.liveAssistantBubble) {
        const txt = state.currentAssistantText.trim();
        finaliseBubble(state.liveAssistantBubble, txt ? `${txt} ✂` : null);
        state.liveAssistantBubble  = null;
        state.currentAssistantText = "";
      }
      setStatus("Interrupted — listening…");
      break;

    case "turn_complete":
      finaliseBubble(state.liveUserBubble, state.currentUserText);
      state.liveUserBubble  = null;
      state.currentUserText = "";

      finaliseBubble(
        state.liveAssistantBubble,
        state.currentAssistantText || "(audio response)"
      );
      state.liveAssistantBubble  = null;
      state.currentAssistantText = "";

      if (state.conversationActive) setStatus("Listening…");
      break;

    case "source_cues":
      renderSourceCues(payload.evidence || []);
      break;

    case "interrupt_ack":
      setStatus("Interrupted — listening…");
      break;

    case "runtime_closed":
      state.runtimeReady = false;
      if (state.conversationActive) {
        state.startupFailed = true;
        setStatus("Gemini Live session closed. Please end and restart.");
        addSystemMessage("Session disconnected. End the conversation and start a new one.");
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
      console.debug("Unhandled server event:", payload);
  }
}

// ── Session management ────────────────────────────────────────────────────────

async function createSession() {
  const title = (els.sessionTitle.value || "").trim() || "Live session";
  const res = await fetch(`${API_BASE}/sessions`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ title }),
  });
  if (!res.ok) throw new Error(`Create session failed: ${res.status}`);

  const data = await res.json();
  state.sessionId     = data.session_id;
  els.sessionId.value = data.session_id;
  setStatus("Session created — click Begin Conversation to start");
  addSystemMessage(`Session created: ${data.session_id}`);
  await loadSources();
}

// ── Main conversation flow ────────────────────────────────────────────────────

async function beginConversation() {
  const sessionId = (els.sessionId.value || "").trim();
  if (!sessionId) throw new Error("Session ID required. Create a session first.");

  state.sessionId          = sessionId;
  state.conversationActive = true;
  state.startupFailed      = false;
  updateButtons();

  loadSources();  // non-blocking refresh

  if (!state.ws || state.ws.readyState !== WebSocket.OPEN) {
    setStatus("Connecting…");
    await openWebSocket(sessionId);
  }

  setStatus("Starting Gemini Live runtime…");
  wsSend({ type: "begin_conversation" });

  await waitFor(
    () => state.runtimeReady || state.startupFailed,
    30_000,
    "Gemini Live startup timed out"
  );
  if (!state.runtimeReady) {
    throw new Error("Gemini Live failed to start. Check API credentials and try again.");
  }

  await startMic();
  setStatus("Listening — speak naturally");
  addSystemMessage("Conversation started. Speak to begin. Click End Conversation when done.");
}

async function endConversation() {
  state.conversationActive = false;

  stopMic();
  stopAssistantAudio();

  finaliseBubble(state.liveUserBubble, state.currentUserText);
  state.liveUserBubble  = null;
  state.currentUserText = "";

  finaliseBubble(state.liveAssistantBubble, state.currentAssistantText);
  state.liveAssistantBubble  = null;
  state.currentAssistantText = "";

  if (state.ws && state.ws.readyState === WebSocket.OPEN) {
    wsSend({ type: "end_conversation" });
    state.ws.close();
  }

  state.runtimeReady = false;
  updateButtons();
  setStatus("Conversation ended");
  addSystemMessage("Conversation ended.");
}

// ── Utility ───────────────────────────────────────────────────────────────────

function waitFor(condition, timeout = 10_000, msg = "Timeout") {
  return new Promise((resolve, reject) => {
    const start = Date.now();
    const id = setInterval(() => {
      if (condition()) { clearInterval(id); resolve(); }
      else if (Date.now() - start > timeout) { clearInterval(id); reject(new Error(msg)); }
    }, 100);
  });
}

// ── Event listeners ───────────────────────────────────────────────────────────

els.createBtn.addEventListener("click", async () => {
  els.createBtn.disabled = true;
  try {
    await createSession();
  } catch (err) {
    setStatus(String(err));
    addSystemMessage(String(err));
  } finally {
    els.createBtn.disabled = false;
  }
});

els.beginBtn.addEventListener("click", async () => {
  try {
    await beginConversation();
  } catch (err) {
    state.conversationActive = false;
    updateButtons();
    setStatus(String(err));
    addSystemMessage(`Error: ${err}`);
    console.error(err);
  }
});

els.endBtn.addEventListener("click", () => {
  endConversation().catch(err => { setStatus(String(err)); console.error(err); });
});

els.fileInput.addEventListener("change", () => {
  const file = els.fileInput.files[0];
  if (file) uploadFile(file);
});

els.webSearchBtn.addEventListener("click", performWebSearch);
els.webSearchInput.addEventListener("keydown", (ev) => {
  if (ev.key === "Enter") performWebSearch();
});

els.addWebBtn.addEventListener("click", addSelectedWebSources);

els.sessionId.addEventListener("change", () => {
  const id = els.sessionId.value.trim();
  if (id) { state.sessionId = id; loadSources(); }
});

updateButtons();
