# LiveNotebookLM

**Real-time voice AI assistant for your documents — powered by Gemini Live API**

LiveNotebookLM reimagines Google NotebookLM as a live, voice-first experience. Upload your PDFs or documents, add web sources, and have a natural spoken conversation about them — no turn buttons, no delays, and full interrupt support. The assistant grounds every answer in your sources using RAG, and your entire conversation history is automatically backed up to Google Cloud Storage.

---

## Architecture Diagram

```
┌──────────────────────────────────────────────────────────────────────────────┐
│                              BROWSER  (Vanilla JS)                           │
│                                                                              │
│  ┌───────────────────┐   ┌──────────────────────┐   ┌────────────────────┐ │
│  │  Mic capture      │   │  Conversation thread │   │  Right panel       │ │
│  │  AudioWorklet     │──▶│  • User bubbles      │   │  • Source manager  │ │
│  │  (audio thread)   │   │  • Agent bubbles     │   │  • Web search      │ │
│  │  PCM-16 @ 16 kHz  │   │  • Live transcripts  │   │  • Citations       │ │
│  └────────┬──────────┘   └──────────────────────┘   │  • Recap           │ │
│           │ base64 audio chunks over WebSocket        └────────────────────┘ │
└───────────┼──────────────────────────────────────────────────────────────────┘
            │  WebSocket  /ws/live/{session_id}
            ▼
┌──────────────────────────────────────────────────────────────────────────────┐
│                      FASTAPI BACKEND  (Google Cloud Run)                     │
│                                                                              │
│  ┌───────────────────────────────────────────────────────────────────────┐  │
│  │  ws_handlers.py  —  WebSocket event router                            │  │
│  │  begin_conversation → audio_chunk → interrupt → end_conversation      │  │
│  │  per-turn state: user_transcript, assistant_transcript, parts         │  │
│  └──────────────────────┬─────────────────────┬───────────────────────┘  │
│                         │                     │                             │
│              ┌──────────▼──────────┐  ┌───────▼────────────────────────┐  │
│              │    LiveRuntime      │  │      LiveOrchestrator           │  │
│              │                     │  │                                 │  │
│              │  Wraps Gemini Live  │  │  record_user_message()          │  │
│              │  session            │  │  record_assistant_message()     │  │
│              │  send_audio_chunk() │  │  prepare_grounded_turn()        │  │
│              │  receive_events()   │  │    → RAG retrieve → citations   │  │
│              │  _receiver_loop()   │  │    → evidence to frontend       │  │
│              └──────────┬──────────┘  └───────┬────────────────────────┘  │
│                         │                     │                             │
│                         │           ┌─────────▼──────────────────────────┐ │
│                         │           │  SessionStore  │  MemoryManager    │ │
│                         │           │  session.json  │  rolling summary  │ │
│                         │           │  messages.json │  recent turns     │ │
│                         │           │  recap.json    │  open questions   │ │
│                         │           └────────────────┴──────────────┬───┘ │
│                         │                                            │      │
└─────────────────────────┼────────────────────────────────────────────┼──────┘
                          │ bidirectional                               │ async
                          │ audio + events                              │ backup
                          ▼                                             ▼
          ┌───────────────────────────┐           ┌────────────────────────────┐
          │   GEMINI LIVE API         │           │  GOOGLE CLOUD STORAGE      │
          │   (Vertex AI)             │           │                            │
          │                           │           │  backups/sessions/         │
          │  gemini-live-2.5-flash-   │           │  └─{session_id}/           │
          │  native-audio             │           │    ├ session.json          │
          │                           │           │    ├ messages.json         │
          │  • Server-side VAD        │           │    ├ memory.json           │
          │  • Streaming TTS audio    │           │    ├ recap.json            │
          │  • input_transcription    │           │    ├ sources.json          │
          │    (user ASR, cumulative) │           │    └ chunks/*.json         │
          │  • output_audio_          │           │                            │
          │    transcription          │           │  sessions/{id}/sources/    │
          │    (agent ASR, word-by-   │           │    (raw uploaded files)    │
          │    word incremental)      │           └────────────────────────────┘
          └───────────────────────────┘

                       RAG PIPELINE  (after every completed turn)
┌──────────────────────────────────────────────────────────────────────────────┐
│                                                                              │
│  Source upload  ──▶  SourceProcessor  ──▶  Vertex AI text-embedding-004    │
│  (PDF / DOCX /         chunk + metadata         (3072 dims)                 │
│   web snippet)                │                      │                      │
│                               │                      ▼                      │
│                               └─────────────▶  Pinecone upsert             │
│                                                 (namespace per session)     │
│                                                                              │
│  User question  ──▶  Vertex AI embedding  ──▶  Pinecone top-k query        │
│  (transcript)                                        │                      │
│                                                      ▼                      │
│                              Retrieved chunks  ──▶  Citations panel        │
│                              (evidence)        ──▶  System instruction     │
│                                                                              │
│  Fallback (Pinecone not configured):  keyword search over local chunks/     │
└──────────────────────────────────────────────────────────────────────────────┘
```

---

## Features

### Real-time voice conversation
- **Unlimited multi-turn voice chat** — speak continuously without pressing any button between turns
- **Natural interrupts** — start speaking while the agent is talking and it stops immediately
- **Server-side VAD** — Gemini detects your speech start and end automatically, no push-to-talk
- **Live transcripts** — your words and the agent's words appear in real time as you speak
- **Energy gate + keepalive** — prevents idle disconnects (codes 1006/1007) by skipping silent audio while sending a sparse keepalive every 3 seconds

### Document understanding and RAG
- **PDF and DOCX upload** — documents are chunked, embedded via Vertex AI, and stored in Pinecone for semantic retrieval
- **Web search sources** — search the web via Google ADK and add result snippets directly as conversation context
- **Up to 10 sources per session** — all sources listed in the sidebar, removable at any time
- **Inline citations** — after each agent turn the right panel shows exactly which document chunks were retrieved, with source name, page number, and snippet
- **In-context fallback** — for sessions without Pinecone, up to 40 000 characters of source content is injected directly into the Gemini system instruction

### Session and memory management
- **Auto-generated session titles** — title is set from the first transcribed question
- **Rolling conversation memory** — `MemoryManager` maintains recent messages, a rolling summary of older turns, open questions, and key topics, all persisted to `memory.json`
- **Session recap** — one-click structured summary of the entire conversation
- **Multi-session sidebar** — all sessions listed, switch between them instantly

### Data durability
- **Local-first storage** — all data written to `sessions/` on disk immediately after each turn
- **Automatic GCS backup** — non-blocking background backup fires after every completed turn
- **Cold-start restore** — on container startup, all GCS-backed sessions missing locally are automatically restored
- **Backup & Restore UI** — `/restore` page lets any user manually restore any cloud-backed session with one click

---

## Technologies Used

| Layer | Technology | Purpose |
|---|---|---|
| **AI model** | Gemini Live 2.5 Flash Native Audio (Vertex AI) | Real-time bidirectional voice |
| **Embeddings** | Vertex AI `text-embedding-004` (3072-dim) | Semantic document search |
| **Vector database** | Pinecone (serverless) | Fast approximate nearest-neighbour retrieval |
| **Backend** | Python 3.11, FastAPI, Uvicorn | Async API server and WebSocket handler |
| **Frontend** | Vanilla JavaScript, WebSocket API | Single-page application, no framework |
| **Audio** | Web Audio API, AudioWorklet | Off-thread mic capture, PCM-16 encoding |
| **Document parsing** | pypdf, python-docx | PDF and DOCX chunking |
| **Web search** | Google ADK `web_search_agent` | Web source discovery |
| **Cloud storage** | Google Cloud Storage | Backup, raw file storage |
| **Infrastructure** | Google Cloud Run, Artifact Registry | Container hosting |
| **IaC** | Terraform | Reproducible cloud provisioning |
| **Container** | Docker (python:3.11-slim) | Portable deployment |
| **Build** | Google Cloud Build | Remote image build and push |

---

## Project Structure

```
LiveNotebookLM/
├── app/
│   ├── main.py                       # FastAPI app, startup restore, routes
│   ├── config.py                     # Settings from environment variables
│   ├── ws_handlers.py                # WebSocket event router (core session loop)
│   ├── live_runtime.py               # Gemini Live API wrapper
│   ├── session_store.py              # Session metadata & message persistence
│   ├── source_store.py               # Source metadata persistence
│   ├── source_processor.py           # PDF/DOCX/web chunking
│   ├── memory_manager.py             # Rolling conversation memory
│   ├── embedding_service.py          # Vertex AI text-embedding-004
│   ├── gcs_backup.py                 # GCS backup & restore
│   ├── gcs_store.py                  # Raw GCS upload helpers
│   ├── web_search_service.py         # ADK-based web search
│   ├── schemas.py                    # Pydantic models
│   ├── routes/
│   │   ├── sessions.py               # REST: CRUD sessions
│   │   ├── sources.py                # REST: upload, web search, add sources
│   │   ├── recap.py                  # REST: generate recap
│   │   └── backup.py                 # REST: GCS backup/restore endpoints
│   ├── live_notebook_agent/
│   │   └── sub_agents/
│   │       ├── live_orchestrator.py  # Turn recording + RAG trigger
│   │       └── retriever.py          # Pinecone + local keyword fallback
│   └── static/
│       ├── index.html                # Single-page application
│       ├── live.js                   # All frontend logic (~1200 lines)
│       ├── mic-processor.js          # AudioWorklet (off-thread mic capture)
│       └── restore.html              # GCS backup/restore page
├── terraform/
│   ├── main.tf                       # Cloud Run, Artifact Registry, GCS, IAM
│   ├── variables.tf
│   ├── outputs.tf
│   └── terraform.tfvars.example
├── Dockerfile
├── deploy.sh                         # One-command deploy script
└── pyproject.toml
```

---

## Public access link

```
https://live-notebook-lm-2cn4ftwhaq-uc.a.run.app/ui
```

---

## Local Setup and rebuild

### Prerequisites

- Python 3.11+
- A Google Cloud project with **Vertex AI API** enabled
- `gcloud` CLI authenticated: `gcloud auth application-default login`
- A GCS bucket for backup storage
- *(Optional)* Pinecone account for semantic vector search

### 1. Clone and install

```bash
git clone https://github.com/GuanqiaoChen/LiveNotebookLM.git
cd LiveNotebookLM
pip install -e .
```

### 2. Configure environment

```bash
cp app/.env.example app/.env
```

Edit `app/.env`:

```env
# Required
GOOGLE_GENAI_USE_VERTEXAI=true
GOOGLE_CLOUD_PROJECT=your-gcp-project-id
GOOGLE_CLOUD_LOCATION=us-central1
LIVE_NOTEBOOK_AGENT_MODEL=gemini-live-2.5-flash-native-audio
GCS_BUCKET=your-gcs-bucket-name

# Optional — enables semantic RAG (falls back to keyword search without it)
PINECONE_API_KEY=your-pinecone-api-key
PINECONE_INDEX_NAME=your-pinecone-index-name
PINECONE_NAMESPACE_PREFIX=live-notebook-lm
```

**Supported Live models:**
| Environment | Model name |
|---|---|
| Vertex AI | `gemini-live-2.5-flash-native-audio` |
| Gemini Direct API | `gemini-2.5-flash-native-audio-preview-12-2025` |

### 3. Pinecone index setup *(if using semantic RAG)*

Create a Pinecone index with:
- **Dimensions:** `3072`
- **Metric:** `cosine`
- **Type:** Serverless

### 4. Run the server

```bash
cd app
uvicorn main:app --host 0.0.0.0 --port 8080 --reload
```

Open **http://localhost:8080/ui**

---

## Cloud Deployment

### One-time setup

**1. Create the service account and grant roles:**

```bash
PROJECT_ID="your-gcp-project-id"

gcloud iam service-accounts create live-notebooklm-sa \
  --project="${PROJECT_ID}" \
  --display-name="LiveNotebookLM Service Account"

for ROLE in \
  roles/aiplatform.user \
  roles/storage.objectAdmin \
  roles/logging.logWriter; do
  gcloud projects add-iam-policy-binding "${PROJECT_ID}" \
    --member="serviceAccount:live-notebooklm-sa@${PROJECT_ID}.iam.gserviceaccount.com" \
    --role="${ROLE}"
done
```

**2. Grant Cloud Build permissions:**

```bash
PROJECT_NUMBER=$(gcloud projects describe "${PROJECT_ID}" \
  --format="value(projectNumber)")

gcloud projects add-iam-policy-binding "${PROJECT_ID}" \
  --member="serviceAccount:${PROJECT_NUMBER}@cloudbuild.gserviceaccount.com" \
  --role="roles/artifactregistry.writer"

gcloud projects add-iam-policy-binding "${PROJECT_ID}" \
  --member="serviceAccount:${PROJECT_NUMBER}@cloudbuild.gserviceaccount.com" \
  --role="roles/run.developer"
```

**3. Install Terraform** (>= 1.0): https://developer.hashicorp.com/terraform/install

### Deploy

```bash
./deploy.sh YOUR_PROJECT_ID us-central1
```

What happens under the hood:

| Step | What it does |
|---|---|
| **1. Terraform init** | Downloads Google provider, initialises state |
| **2. Terraform apply (placeholder)** | Creates Artifact Registry repo, GCS bucket, Cloud Run service with a hello-world image |
| **3. Cloud Build** | Builds your Docker image remotely and pushes it to Artifact Registry |
| **4. Terraform apply (real image)** | Updates Cloud Run to run your actual container |

The Cloud Run URL is printed at the end. Access your app at:
```
https://<service-url>/ui
```

---

## Environment Variables Reference

| Variable | Required | Default | Description |
|---|---|---|---|
| `GOOGLE_CLOUD_PROJECT` | ✅ | — | GCP project ID |
| `GOOGLE_CLOUD_LOCATION` | ✅ | — | Region, e.g. `us-central1` |
| `GOOGLE_GENAI_USE_VERTEXAI` | ✅ | — | Must be `true` |
| `LIVE_NOTEBOOK_AGENT_MODEL` | ✅ | — | Gemini Live model name |
| `GCS_BUCKET` | ✅ | — | Bucket for file storage and backups |
| `PINECONE_API_KEY` | ☐ | `None` | Enables semantic vector search |
| `PINECONE_INDEX_NAME` | ☐ | `None` | Pinecone index name (3072-dim cosine) |
| `PINECONE_NAMESPACE_PREFIX` | ☐ | `live-notebook-lm` | Pinecone namespace prefix |
| `PORT` | ☐ | `8080` | Server port |
| `SESSIONS_DIR` | ☐ | `../sessions` | Local session data directory |
| `MAX_SOURCES_PER_SESSION` | ☐ | `10` | Maximum sources per session |

---

## Findings and Learnings

Building a real-time voice application on Gemini Live API surfaced a set of challenges that simply don't appear in standard REST or chat-based AI integrations. Here are the key technical learnings from this project.

### 1. Gemini Live is a stateful streaming protocol, not a request-response API

The Gemini Live API works over a persistent bidirectional WebSocket. There is no "send a request, get a response." You stream raw PCM-16 audio continuously and receive a stream of interleaved server events — audio chunks, transcription fragments, VAD signals, and turn markers. Designing the backend around an asyncio event queue (`_receiver_loop` → `asyncio.Queue` → `receive_events()` generator) was essential to keep the connection alive while forwarding events to the client WebSocket without blocking the event loop.

### 2. Event ordering inside a single server message matters

One of the most subtle bugs encountered: Gemini sometimes delivers `turn_complete` and `output_audio_transcription` in the **same server content object**. The order in which you enqueue these events to downstream consumers is entirely up to you. If you emit `turn_complete` first, the frontend finalises the transcript bubble before the final words arrive — producing bubbles showing only one or two words per response. The fix was simple but non-obvious: always emit `assistant_transcript` before `turn_complete`, regardless of field order in the Gemini response object.

### 3. Silence kills the connection — but so does fake silence prevention

Two distinct close codes emerged in testing:
- **1007 (invalid payload):** Gemini closes the session if you send too many silent PCM frames. An energy gate (RMS threshold) was added to skip silent chunks.
- **1006 (abnormal closure):** Gemini closes the session if it receives **no data at all** for ~3 seconds. Simply stopping all audio during silence triggered this. The solution was a sparse keepalive — send one real (but very quiet) audio chunk every 3 seconds during silence, maintaining the connection without triggering the 1007 idle timeout.

### 4. `send_client_content()` races with live audio and breaks interrupts

The initial RAG design injected retrieved document context back into Gemini after each `turn_complete` using `send_client_content()`. However, when the user speaks immediately after the agent finishes (or interrupts mid-response), the text injection and the live audio from `send_realtime_input()` arrive at Gemini simultaneously and compete for the same turn. Gemini silently drops the user's audio — the user has to repeat their question. The fix: remove the post-turn context injection entirely. Document context is instead injected once via the system instruction at session start.

### 5. Cumulative vs. incremental transcripts require opposite handling

Gemini sends two types of transcription:
- **`input_transcription`** (user speech): **cumulative** — each event contains the full text so far. Must replace, not append.
- **`output_audio_transcription`** (agent speech): **incremental** — each event contains a new word or phrase. Must append, not replace.

Conflating these caused the assistant transcript to double-print every word ("Hello Hello there there…"). Splitting into separate handlers with correct semantics for each resolved it.

### 6. AudioWorklet is necessary for frame-accurate mic capture

The browser's deprecated `ScriptProcessorNode` runs on the main thread. Under heavy DOM updates (live transcript rendering, concurrent audio playback), it drops frames. Moving capture to an **AudioWorklet** (`mic-processor.js`) runs it on the dedicated audio rendering thread with zero-copy `ArrayBuffer` transfer back to the main thread for encoding. This eliminated dropped audio frames during fast conversational exchanges.

### 7. RAG for live voice requires a different retrieval strategy than RAG for chat

In a chat RAG system you have time to retrieve chunks between turns. In a live voice session the user is already speaking by the time the previous turn ends — there is no retrieval window. The approach used here retrieves chunks **asynchronously after `turn_complete`** (fire-and-forget) and uses them to: (a) push source cue evidence to the frontend citations panel immediately, and (b) enrich the system instruction at session start with full document content for smaller sets. This avoids the turn-racing problem while still surfacing citations in real time.

### 8. Stateless containers need a data recovery story from day one

Cloud Run containers are ephemeral — every cold start begins with an empty filesystem. Building the GCS backup system first (rather than later) proved essential: every completed turn triggers a non-blocking background backup of all session files. On container cold start, a FastAPI lifespan hook restores all GCS-backed sessions that aren't present locally before the first request is served. User data survives container restarts transparently, with zero user action required.

---

## License

MIT
