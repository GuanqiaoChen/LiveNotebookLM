from __future__ import annotations

import asyncio
import base64
import json

from fastapi import WebSocket, WebSocketDisconnect

from app.live_runtime import LiveRuntime
from app.live_notebook_agent.sub_agents.live_orchestrator import LiveOrchestrator
from app.session_store import SessionStore


async def handle_live_websocket(websocket: WebSocket, session_id: str) -> None:
    await websocket.accept()

    session_store = SessionStore()
    orchestrator = LiveOrchestrator()

    try:
        session_store.get_session_metadata(session_id)
    except FileNotFoundError:
        await websocket.send_json(
            {
                "type": "error",
                "message": f"Session not found: {session_id}",
            }
        )
        await websocket.close(code=4404)
        return

    runtime: LiveRuntime | None = None
    runtime_ready = False
    receiver_task: asyncio.Task | None = None
    runtime_closed_event = asyncio.Event()

    current_user_text_hint = ""
    final_user_text = ""
    assistant_text_parts: list[str] = []

    try:
        await websocket.send_json(
            {
                "type": "connected",
                "session_id": session_id,
            }
        )

        while True:
            message = await websocket.receive()

            if message.get("bytes") is not None:
                if runtime is not None and runtime_ready and not runtime_closed_event.is_set():
                    try:
                        await runtime.send_audio_chunk(message["bytes"])
                    except Exception as exc:
                        await websocket.send_json(
                            {
                                "type": "runtime_error",
                                "message": f"send_audio_chunk failed: {type(exc).__name__}: {exc}",
                            }
                        )
                        runtime_ready = False
                        runtime_closed_event.set()
                else:
                    await websocket.send_json(
                        {
                            "type": "runtime_error",
                            "message": "Live runtime is closed or not ready.",
                        }
                    )
                continue

            raw_text = message.get("text")
            if raw_text is None:
                continue

            payload = json.loads(raw_text)
            event_type = payload.get("type")

            if event_type == "session_start":
                if runtime is None or runtime_closed_event.is_set():
                    runtime_closed_event = asyncio.Event()
                    await websocket.send_json({"type": "runtime_connecting"})
                    runtime = LiveRuntime()
                    await asyncio.wait_for(runtime.connect(), timeout=30)
                    runtime_ready = True

                    receiver_task = asyncio.create_task(
                        _forward_runtime_events(
                            websocket=websocket,
                            runtime=runtime,
                            assistant_text_parts=assistant_text_parts,
                            runtime_closed_event=runtime_closed_event,
                        )
                    )

                await websocket.send_json(
                    {
                        "type": "runtime_ready",
                        "session_id": session_id,
                    }
                )

            elif event_type == "start_turn":
                current_user_text_hint = (payload.get("text_hint") or "").strip()
                final_user_text = ""
                assistant_text_parts.clear()

                if not runtime_ready or runtime is None or runtime_closed_event.is_set():
                    await websocket.send_json(
                        {
                            "type": "runtime_error",
                            "message": "Live runtime is not ready. Send session_start first.",
                        }
                    )
                    continue

                if current_user_text_hint:
                    grounded = orchestrator.prepare_grounded_turn(
                        session_id=session_id,
                        user_text=current_user_text_hint,
                    )

                    await websocket.send_json(
                        {
                            "type": "source_cues",
                            "evidence": grounded["evidence"],
                        }
                    )

            elif event_type == "audio_chunk":
                if not runtime_ready or runtime is None or runtime_closed_event.is_set():
                    await websocket.send_json(
                        {
                            "type": "runtime_error",
                            "message": "Live runtime already closed before audio chunk.",
                        }
                    )
                    continue

                b64 = payload.get("data", "")
                mime_type = payload.get("mime_type", "audio/pcm;rate=16000")
                if not b64:
                    continue

                audio_bytes = base64.b64decode(b64)
                try:
                    await runtime.send_audio_chunk(audio_bytes, mime_type=mime_type)
                except Exception as exc:
                    await websocket.send_json(
                        {
                            "type": "runtime_error",
                            "message": f"send_audio_chunk failed: {type(exc).__name__}: {exc}",
                        }
                    )
                    runtime_ready = False
                    runtime_closed_event.set()

            elif event_type == "audio_stream_end":
                if runtime is not None and runtime_ready and not runtime_closed_event.is_set():
                    try:
                        await runtime.end_audio_stream()
                    except Exception as exc:
                        await websocket.send_json(
                            {
                                "type": "runtime_error",
                                "message": f"end_audio_stream failed: {type(exc).__name__}: {exc}",
                            }
                        )
                        runtime_ready = False
                        runtime_closed_event.set()

            elif event_type == "commit_user_text":
                final_user_text = (payload.get("text") or "").strip()

            elif event_type == "interrupt":
                await websocket.send_json({"type": "interrupt_ack"})

            elif event_type == "close_session":
                break

        if receiver_task is not None:
            receiver_task.cancel()
            try:
                await receiver_task
            except asyncio.CancelledError:
                pass

        user_text_to_save = final_user_text or current_user_text_hint
        assistant_text_to_save = " ".join(
            part.strip() for part in assistant_text_parts if part.strip()
        ).strip()

        if user_text_to_save:
            orchestrator.record_user_message(session_id, user_text_to_save)

        if assistant_text_to_save:
            orchestrator.record_assistant_message(
                session_id=session_id,
                content=assistant_text_to_save,
                citations=[],
            )

    except WebSocketDisconnect:
        pass
    except Exception as exc:
        try:
            await websocket.send_json(
                {
                    "type": "error",
                    "message": f"{type(exc).__name__}: {exc}",
                }
            )
        except Exception:
            pass
    finally:
        if runtime is not None:
            await runtime.close()
        try:
            await websocket.close()
        except Exception:
            pass


async def _forward_runtime_events(
    websocket: WebSocket,
    runtime: LiveRuntime,
    assistant_text_parts: list[str],
    runtime_closed_event: asyncio.Event,
) -> None:
    async for event in runtime.receive_events():
        event_type = event.get("type")

        if event_type in {"assistant_text", "assistant_transcript"}:
            text = (event.get("text") or "").strip()
            if text:
                assistant_text_parts.append(text)

        if event_type in {"runtime_error", "runtime_closed"}:
            runtime_closed_event.set()

        await websocket.send_json(event)