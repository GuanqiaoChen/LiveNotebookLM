from __future__ import annotations

import asyncio
import base64
import json
from pathlib import Path

from fastapi import WebSocket, WebSocketDisconnect

from app.config import get_settings
from app.live_runtime import LiveRuntime
from app.live_notebook_agent.sub_agents.live_orchestrator import LiveOrchestrator
from app.session_store import SessionStore


# ── System instruction builder ────────────────────────────────────────────────

def _build_system_instruction(session_id: str, orchestrator: LiveOrchestrator) -> str:
    """
    Build a Gemini Live system instruction that includes:
    - Role and behavioural guidelines
    - Names of uploaded sources
    - Actual chunk content (up to 40 k chars) for in-context grounding
    """
    settings = get_settings()
    sources = orchestrator.get_session_sources(session_id)

    base = (
        "You are LiveNotebookLM, a real-time voice AI assistant powered by Google Gemini. "
        "You help users explore and discuss their uploaded documents through natural, "
        "unlimited multi-turn conversation.\n\n"
        "Behaviour guidelines:\n"
        "- Respond conversationally and concisely, optimised for spoken delivery\n"
        "- Keep answers to 2-4 sentences unless the user asks for more detail\n"
        "- Ground responses in the uploaded source content whenever relevant\n"
        "- If sources do not contain the answer, say so clearly\n"
        "- If the user interrupts you mid-response, stop immediately and listen\n"
        "- The conversation continues indefinitely — NEVER end it yourself\n"
        "- When the user is silent, simply wait for their next question\n"
        "- Use natural spoken transitions (e.g. 'Based on the document…')\n"
    )

    if not sources:
        return base + "\nNo sources have been uploaded for this session yet."

    source_names: list[str] = []
    content_parts: list[str] = []
    total_chars = 0
    max_content_chars = 40_000  # ≈10k tokens

    for src in sources:
        source_id = src.get("source_id", "")
        source_name = (
            src.get("original_filename")
            or src.get("display_name")
            or "unknown"
        )
        source_names.append(source_name)

        chunks_path = (
            Path(settings.sessions_dir) / session_id / "chunks" / f"{source_id}.json"
        )
        if not chunks_path.exists():
            continue

        try:
            chunks = json.loads(chunks_path.read_text(encoding="utf-8"))
            content_parts.append(f"\n=== {source_name} ===")
            for chunk in chunks:
                text = (chunk.get("text") or "").strip()
                if not text:
                    continue
                if total_chars + len(text) > max_content_chars:
                    content_parts.append("... [additional content truncated due to length]")
                    break
                content_parts.append(text)
                total_chars += len(text)
        except Exception:
            pass

        if total_chars >= max_content_chars:
            break

    names_str = ", ".join(source_names)
    instruction = base + f"\nUploaded sources for this session: {names_str}\n"
    if content_parts:
        instruction += "\nSource content (use this to ground your answers):\n"
        instruction += "\n".join(content_parts)

    return instruction


# ── WebSocket handler ─────────────────────────────────────────────────────────

async def handle_live_websocket(websocket: WebSocket, session_id: str) -> None:
    await websocket.accept()

    # Send "connected" immediately so the frontend's 10-second WS timeout does not
    # fire while we do heavier initialisation (Pinecone Index lookup, etc.).
    await websocket.send_json({"type": "connected", "session_id": session_id})

    session_store = SessionStore()

    # Validate session exists
    try:
        session_store.get_session_metadata(session_id)
    except FileNotFoundError:
        await websocket.send_json(
            {"type": "error", "message": f"Session not found: {session_id}"}
        )
        await websocket.close(code=4404)
        return

    # Lazy-init: LiveOrchestrator triggers Pinecone.Index() which makes a
    # network call.  Defer until begin_conversation so it never blocks the
    # initial handshake.
    orchestrator: LiveOrchestrator | None = None

    runtime: LiveRuntime | None = None
    runtime_ready = False
    receiver_task: asyncio.Task | None = None
    runtime_closed_event = asyncio.Event()

    # Per-turn mutable state shared with the receiver task.
    # user_transcript: rolling cumulative text (replaced, not appended)
    # assistant_parts: accumulated text fragments (appended)
    turn_state: dict = {
        "user_transcript": "",
        "assistant_parts": [],
    }

    async def _start_runtime() -> None:
        nonlocal runtime, runtime_ready, receiver_task, runtime_closed_event, orchestrator

        # Initialise orchestrator here (not at WS accept time) to avoid
        # blocking the handshake with Pinecone network calls.
        if orchestrator is None:
            orchestrator = LiveOrchestrator()

        runtime_closed_event = asyncio.Event()
        turn_state["user_transcript"] = ""
        turn_state["assistant_parts"].clear()

        await websocket.send_json({"type": "runtime_connecting"})

        system_instruction = _build_system_instruction(session_id, orchestrator)
        runtime = LiveRuntime()
        await asyncio.wait_for(
            runtime.connect(system_instruction=system_instruction), timeout=30
        )
        runtime_ready = True

        receiver_task = asyncio.create_task(
            _forward_runtime_events(
                websocket=websocket,
                runtime=runtime,
                turn_state=turn_state,
                runtime_closed_event=runtime_closed_event,
                session_id=session_id,
                orchestrator=orchestrator,
            )
        )

    try:
        while True:
            message = await websocket.receive()

            # ── Raw binary audio (legacy support) ────────────────────────────
            if message.get("bytes") is not None:
                if runtime_ready and not runtime_closed_event.is_set():
                    try:
                        await runtime.send_audio_chunk(message["bytes"])
                    except Exception as exc:
                        await websocket.send_json(
                            {"type": "runtime_error", "message": str(exc)}
                        )
                continue

            raw_text = message.get("text")
            if raw_text is None:
                continue

            payload = json.loads(raw_text)
            event_type = payload.get("type")

            # ── begin_conversation / session_start ────────────────────────────
            if event_type in {"begin_conversation", "session_start"}:
                if runtime is None or runtime_closed_event.is_set():
                    await _start_runtime()
                await websocket.send_json(
                    {"type": "runtime_ready", "session_id": session_id}
                )

            # ── audio_chunk ───────────────────────────────────────────────────
            elif event_type == "audio_chunk":
                if not runtime_ready or runtime_closed_event.is_set():
                    continue

                b64 = payload.get("data", "")
                mime_type = payload.get("mime_type", "audio/pcm;rate=16000")
                if not b64:
                    continue

                try:
                    audio_bytes = base64.b64decode(b64)
                    await runtime.send_audio_chunk(audio_bytes, mime_type=mime_type)
                except Exception as exc:
                    await websocket.send_json(
                        {"type": "runtime_error", "message": str(exc)}
                    )
                    runtime_ready = False
                    runtime_closed_event.set()

            # ── audio_stream_end ──────────────────────────────────────────────
            elif event_type == "audio_stream_end":
                if runtime_ready and not runtime_closed_event.is_set():
                    try:
                        await runtime.end_audio_stream()
                    except Exception as exc:
                        await websocket.send_json(
                            {"type": "runtime_error", "message": str(exc)}
                        )

            # ── interrupt ─────────────────────────────────────────────────────
            elif event_type == "interrupt":
                # Save whatever partial turn exists
                if orchestrator is not None:
                    user_text = turn_state["user_transcript"]
                    assistant_text = " ".join(turn_state["assistant_parts"]).strip()
                    if user_text:
                        orchestrator.record_user_message(
                            session_id, user_text, interrupted=True
                        )
                    if assistant_text:
                        orchestrator.record_assistant_message(
                            session_id, assistant_text, interrupted=True
                        )
                turn_state["user_transcript"] = ""
                turn_state["assistant_parts"].clear()

                await websocket.send_json({"type": "interrupt_ack"})

            # ── legacy start_turn ─────────────────────────────────────────────
            elif event_type == "start_turn":
                text_hint = (payload.get("text_hint") or "").strip()
                turn_state["user_transcript"] = ""
                turn_state["assistant_parts"].clear()

                if text_hint and runtime_ready and not runtime_closed_event.is_set():
                    try:
                        grounded = orchestrator.prepare_grounded_turn(
                            session_id=session_id, user_text=text_hint
                        )
                        await websocket.send_json(
                            {"type": "source_cues", "evidence": grounded["evidence"]}
                        )
                    except Exception:
                        pass

            # ── legacy commit_user_text ───────────────────────────────────────
            elif event_type == "commit_user_text":
                text = (payload.get("text") or "").strip()
                if text:
                    turn_state["user_transcript"] = text

            # ── end_conversation / close_session ──────────────────────────────
            elif event_type in {"end_conversation", "close_session"}:
                # Flush any pending turn data before closing
                if orchestrator is not None:
                    user_text = turn_state["user_transcript"]
                    assistant_text = " ".join(turn_state["assistant_parts"]).strip()
                    if user_text:
                        orchestrator.record_user_message(session_id, user_text)
                    if assistant_text:
                        orchestrator.record_assistant_message(session_id, assistant_text)
                break

    except WebSocketDisconnect:
        pass
    except Exception as exc:
        try:
            await websocket.send_json(
                {"type": "error", "message": f"{type(exc).__name__}: {exc}"}
            )
        except Exception:
            pass
    finally:
        if receiver_task is not None:
            receiver_task.cancel()
            try:
                await receiver_task
            except asyncio.CancelledError:
                pass

        if runtime is not None:
            await runtime.close()

        try:
            await websocket.close()
        except Exception:
            pass


# ── Runtime event forwarder ───────────────────────────────────────────────────

async def _forward_runtime_events(
    websocket: WebSocket,
    runtime: LiveRuntime,
    turn_state: dict,
    runtime_closed_event: asyncio.Event,
    session_id: str,
    orchestrator: LiveOrchestrator,
) -> None:
    """
    Consume events from LiveRuntime, update per-turn state, persist completed
    turns, trigger RAG source-cues, and forward every event to the frontend.
    """
    async for event in runtime.receive_events():
        event_type = event.get("type")

        if event_type == "user_transcript":
            text = (event.get("text") or "").strip()
            if text:
                # Gemini sends cumulative rolling transcripts → always replace
                turn_state["user_transcript"] = text

        elif event_type in {"assistant_text", "assistant_transcript"}:
            text = (event.get("text") or "").strip()
            if text:
                turn_state["assistant_parts"].append(text)

        elif event_type == "assistant_interrupted":
            # VAD detected user speaking mid-response: flush partial assistant turn
            assistant_text = " ".join(turn_state["assistant_parts"]).strip()
            if assistant_text:
                orchestrator.record_assistant_message(
                    session_id, assistant_text, interrupted=True
                )
            turn_state["assistant_parts"].clear()

        elif event_type == "turn_complete":
            user_text = turn_state["user_transcript"]
            assistant_text = " ".join(turn_state["assistant_parts"]).strip()

            # Persist completed turn
            if user_text:
                orchestrator.record_user_message(session_id, user_text)
            if assistant_text:
                orchestrator.record_assistant_message(session_id, assistant_text)

            # Trigger RAG to populate source cues panel
            if user_text:
                try:
                    grounded = orchestrator.prepare_grounded_turn(
                        session_id=session_id, user_text=user_text
                    )
                    if grounded.get("evidence"):
                        await websocket.send_json(
                            {"type": "source_cues", "evidence": grounded["evidence"]}
                        )
                except Exception:
                    pass

            turn_state["user_transcript"] = ""
            turn_state["assistant_parts"].clear()

        if event_type in {"runtime_error", "runtime_closed"}:
            runtime_closed_event.set()

        try:
            await websocket.send_json(event)
        except Exception:
            break
