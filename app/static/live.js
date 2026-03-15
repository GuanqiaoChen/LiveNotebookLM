const API_BASE = window.location.origin;
const WS_BASE = window.location.origin.replace(/^http/, "ws");

const ASSISTANT_PCM_SAMPLE_RATE = 24000;
const USER_PCM_SAMPLE_RATE = 16000;

const state = {
  ws: null,
  sessionId: "",
  runtimeReady: false,
  audioContext: null,
  playbackTime: 0,
  activeSources: [],
  mediaStream: null,
  mediaSourceNode: null,
  processorNode: null,
  userTranscriptLatest: "",
  assistantTranscriptLatest: "",
  isRecording: false,
};

const els = {
  sessionTitle: document.getElementById("sessionTitle"),
  sessionId: document.getElementById("sessionId"),
  textHint: document.getElementById("textHint"),
  createSessionBtn: document.getElementById("createSessionBtn"),
  connectBtn: document.getElementById("connectBtn"),
  startTalkBtn: document.getElementById("startTalkBtn"),
  stopTalkBtn: document.getElementById("stopTalkBtn"),
  interruptBtn: document.getElementById("interruptBtn"),
  closeBtn: document.getElementById("closeBtn"),
  statusBox: document.getElementById("statusBox"),
  messages: document.getElementById("messages"),
  sourceCues: document.getElementById("sourceCues"),
};

function setStatus(text) {
  els.statusBox.textContent = text;
}

function addMessage(role, text) {
  const div = document.createElement("div");
  div.className = `msg ${role}`;
  div.textContent = text;
  els.messages.appendChild(div);
  els.messages.scrollTop = els.messages.scrollHeight;
}

function renderSourceCues(evidence) {
  els.sourceCues.innerHTML = "";
  state.activeSources = evidence || [];

  if (!state.activeSources.length) {
    const empty = document.createElement("div");
    empty.className = "muted";
    empty.textContent = "No source cues";
    els.sourceCues.appendChild(empty);
    return;
  }

  for (const item of state.activeSources) {
    const div = document.createElement("div");
    div.className = "source";

    const title = document.createElement("div");
    title.innerHTML = `<strong>${item.source_name || "unknown source"}</strong>`;
    div.appendChild(title);

    const meta = document.createElement("div");
    meta.className = "small";
    meta.textContent = [
      item.page != null ? `page ${item.page}` : null,
      item.section ? `section ${item.section}` : null,
      item.score != null ? `score ${Number(item.score).toFixed(3)}` : null,
    ].filter(Boolean).join(" · ");
    div.appendChild(meta);

    const snippet = document.createElement("div");
    snippet.style.marginTop = "8px";
    snippet.textContent = item.text || "";
    div.appendChild(snippet);

    els.sourceCues.appendChild(div);
  }
}

async function createSession() {
  const title = els.sessionTitle.value.trim() || "Live session";
  const res = await fetch(`${API_BASE}/sessions`, {
    method: "POST",
    headers: {"Content-Type": "application/json"},
    body: JSON.stringify({ title }),
  });
  if (!res.ok) {
    throw new Error(`Create session failed: ${res.status}`);
  }
  const data = await res.json();
  state.sessionId = data.session_id;
  els.sessionId.value = data.session_id;
  setStatus(`Created session: ${data.session_id}`);
  addMessage("system", `Created session: ${data.session_id}`);
}

function connectWebSocket() {
  const sessionId = els.sessionId.value.trim();
  if (!sessionId) {
    throw new Error("Session ID is required");
  }

  state.sessionId = sessionId;

  if (state.ws && state.ws.readyState === WebSocket.OPEN) {
    return;
  }

  const ws = new WebSocket(`${WS_BASE}/ws/live/${sessionId}`);
  state.ws = ws;

  ws.onopen = () => {
    setStatus("WebSocket opening...");
  };

  ws.onmessage = (event) => {
    const payload = JSON.parse(event.data);

    switch (payload.type) {
      case "connected":
        setStatus(`Connected to session ${payload.session_id}`);
        addMessage("system", `Connected to session ${payload.session_id}`);
        break;

      case "runtime_connecting":
        setStatus("Connecting Gemini Live runtime...");
        break;

      case "runtime_ready":
        state.runtimeReady = true;
        setStatus(`Runtime ready: ${payload.session_id}`);
        break;

      case "source_cues":
        renderSourceCues(payload.evidence || []);
        setStatus("Source cues received");
        break;

      case "user_transcript":
        state.userTranscriptLatest = payload.text || "";
        break;

      case "assistant_transcript":
      case "assistant_text":
        state.assistantTranscriptLatest = payload.text || "";
        break;

      case "assistant_audio_chunk":
        enqueueAssistantAudio(payload.data);
        break;

      case "turn_complete":
        finalizeTurnTexts();
        setStatus("Turn complete");
        break;

      case "runtime_error":
      case "error":
        setStatus(`${payload.type}: ${payload.message}`);
        addMessage("system", `${payload.type}: ${payload.message}`);
        break;

      case "runtime_closed":
        state.runtimeReady = false;
        setStatus("Runtime closed");
        break;

      case "interrupt_ack":
        setStatus("Interrupt acknowledged");
        break;

      default:
        console.log("Unhandled event:", payload);
    }
  };

  ws.onclose = () => {
    state.runtimeReady = false;
    setStatus("WebSocket closed");
  };

  ws.onerror = () => {
    setStatus("WebSocket error");
  };
}

function wsSend(obj) {
  if (!state.ws || state.ws.readyState !== WebSocket.OPEN) {
    throw new Error("WebSocket is not open");
  }
  state.ws.send(JSON.stringify(obj));
}

async function ensureAudioContext() {
  if (!state.audioContext) {
    state.audioContext = new (window.AudioContext || window.webkitAudioContext)();
  }
  if (state.audioContext.state === "suspended") {
    await state.audioContext.resume();
  }
}

function decodeBase64ToBytes(base64) {
  const binary = atob(base64);
  const len = binary.length;
  const bytes = new Uint8Array(len);
  for (let i = 0; i < len; i++) {
    bytes[i] = binary.charCodeAt(i);
  }
  return bytes;
}

function pcm16ToFloat32(uint8Bytes) {
  const view = new DataView(uint8Bytes.buffer, uint8Bytes.byteOffset, uint8Bytes.byteLength);
  const samples = uint8Bytes.byteLength / 2;
  const float32 = new Float32Array(samples);

  for (let i = 0; i < samples; i++) {
    const s = view.getInt16(i * 2, true);
    float32[i] = s / 32768;
  }
  return float32;
}

async function enqueueAssistantAudio(base64Data) {
  await ensureAudioContext();

  const bytes = decodeBase64ToBytes(base64Data);
  const samples = pcm16ToFloat32(bytes);

  const audioBuffer = state.audioContext.createBuffer(1, samples.length, ASSISTANT_PCM_SAMPLE_RATE);
  audioBuffer.copyToChannel(samples, 0);

  const source = state.audioContext.createBufferSource();
  source.buffer = audioBuffer;
  source.connect(state.audioContext.destination);

  const now = state.audioContext.currentTime;
  if (state.playbackTime < now) {
    state.playbackTime = now;
  }

  source.start(state.playbackTime);
  state.playbackTime += audioBuffer.duration;
}

function stopAssistantPlayback() {
  if (!state.audioContext) return;
  state.playbackTime = state.audioContext.currentTime;
}

function downsampleBuffer(buffer, inputSampleRate, targetSampleRate) {
  if (targetSampleRate === inputSampleRate) {
    return buffer;
  }

  const ratio = inputSampleRate / targetSampleRate;
  const newLength = Math.round(buffer.length / ratio);
  const result = new Float32Array(newLength);

  let offsetResult = 0;
  let offsetBuffer = 0;
  while (offsetResult < result.length) {
    const nextOffsetBuffer = Math.round((offsetResult + 1) * ratio);
    let accum = 0;
    let count = 0;

    for (let i = offsetBuffer; i < nextOffsetBuffer && i < buffer.length; i++) {
      accum += buffer[i];
      count++;
    }

    result[offsetResult] = count > 0 ? accum / count : 0;
    offsetResult++;
    offsetBuffer = nextOffsetBuffer;
  }

  return result;
}

function floatTo16BitPCM(float32Array) {
  const buffer = new ArrayBuffer(float32Array.length * 2);
  const view = new DataView(buffer);

  let offset = 0;
  for (let i = 0; i < float32Array.length; i++, offset += 2) {
    let s = Math.max(-1, Math.min(1, float32Array[i]));
    view.setInt16(offset, s < 0 ? s * 0x8000 : s * 0x7fff, true);
  }
  return new Uint8Array(buffer);
}

async function startTalking() {
  await ensureAudioContext();

  if (!state.ws || state.ws.readyState !== WebSocket.OPEN) {
    connectWebSocket();
    await waitForConnected();
  }

  if (!state.runtimeReady) {
    wsSend({ type: "session_start" });
    await waitForRuntimeReady();
  }

  stopAssistantPlayback();
  wsSend({ type: "interrupt" });

  const textHint = els.textHint.value.trim();
  wsSend({
    type: "start_turn",
    text_hint: textHint,
  });

  const stream = await navigator.mediaDevices.getUserMedia({
    audio: {
      channelCount: 1,
      echoCancellation: true,
      noiseSuppression: true,
      autoGainControl: true,
    }
  });

  state.mediaStream = stream;

  const source = state.audioContext.createMediaStreamSource(stream);
  const processor = state.audioContext.createScriptProcessor(4096, 1, 1);

  state.mediaSourceNode = source;
  state.processorNode = processor;

  processor.onaudioprocess = (event) => {
    if (!state.isRecording) return;
    if (!state.ws || state.ws.readyState !== WebSocket.OPEN) return;
    if (!state.runtimeReady) return;

    const input = event.inputBuffer.getChannelData(0);
    const downsampled = downsampleBuffer(input, state.audioContext.sampleRate, USER_PCM_SAMPLE_RATE);
    const pcmBytes = floatTo16BitPCM(downsampled);

    wsSend({
      type: "audio_chunk",
      mime_type: "audio/pcm;rate=16000",
      data: btoa(String.fromCharCode(...pcmBytes)),
    });
  };

  source.connect(processor);
  processor.connect(state.audioContext.destination);

  state.isRecording = true;
  els.startTalkBtn.disabled = true;
  els.stopTalkBtn.disabled = false;
  setStatus("Recording...");
}

function stopTalking() {
  state.isRecording = false;

  if (state.processorNode) {
    state.processorNode.disconnect();
    state.processorNode.onaudioprocess = null;
    state.processorNode = null;
  }

  if (state.mediaSourceNode) {
    state.mediaSourceNode.disconnect();
    state.mediaSourceNode = null;
  }

  if (state.mediaStream) {
    for (const track of state.mediaStream.getTracks()) {
      track.stop();
    }
    state.mediaStream = null;
  }

  if (state.ws && state.ws.readyState === WebSocket.OPEN) {
    wsSend({ type: "audio_stream_end" });
  }

  els.startTalkBtn.disabled = false;
  els.stopTalkBtn.disabled = true;
  setStatus("Audio stream ended");
}

function finalizeTurnTexts() {
  const userText = state.userTranscriptLatest || els.textHint.value.trim();
  const assistantText = state.assistantTranscriptLatest || "(assistant audio returned)";

  if (userText) {
    addMessage("user", userText);
  }
  if (assistantText) {
    addMessage("assistant", assistantText);
  }

  if (state.ws && state.ws.readyState === WebSocket.OPEN && userText) {
    wsSend({
      type: "commit_user_text",
      text: userText,
    });
  }

  state.userTranscriptLatest = "";
  state.assistantTranscriptLatest = "";
}

function interruptCurrentTurn() {
  stopAssistantPlayback();
  if (state.ws && state.ws.readyState === WebSocket.OPEN) {
    wsSend({ type: "interrupt" });
  }
}

function closeSession() {
  stopTalking();
  if (state.ws && state.ws.readyState === WebSocket.OPEN) {
    wsSend({ type: "close_session" });
    state.ws.close();
  }
}

function waitForConnected() {
  return new Promise((resolve, reject) => {
    const started = Date.now();

    const timer = setInterval(() => {
      if (state.ws && state.ws.readyState === WebSocket.OPEN) {
        clearInterval(timer);
        resolve();
        return;
      }
      if (Date.now() - started > 10000) {
        clearInterval(timer);
        reject(new Error("WebSocket connect timeout"));
      }
    }, 100);
  });
}

function waitForRuntimeReady() {
  return new Promise((resolve, reject) => {
    const started = Date.now();

    const timer = setInterval(() => {
      if (state.runtimeReady) {
        clearInterval(timer);
        resolve();
        return;
      }
      if (Date.now() - started > 20000) {
        clearInterval(timer);
        reject(new Error("Runtime ready timeout"));
      }
    }, 100);
  });
}

els.createSessionBtn.addEventListener("click", async () => {
  try {
    await createSession();
  } catch (err) {
    setStatus(String(err));
  }
});

els.connectBtn.addEventListener("click", () => {
  try {
    connectWebSocket();
  } catch (err) {
    setStatus(String(err));
  }
});

els.startTalkBtn.addEventListener("click", async () => {
  try {
    await startTalking();
  } catch (err) {
    setStatus(String(err));
    console.error(err);
  }
});

els.stopTalkBtn.addEventListener("click", () => {
  stopTalking();
});

els.interruptBtn.addEventListener("click", () => {
  interruptCurrentTurn();
});

els.closeBtn.addEventListener("click", () => {
  closeSession();
});