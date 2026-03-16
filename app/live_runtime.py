from __future__ import annotations

import asyncio
import base64
from typing import Any, AsyncIterator, Optional

from google import genai
from google.genai import types
from google.genai import errors as genai_errors
from websockets.exceptions import ConnectionClosedError, ConnectionClosedOK

from app.config import get_settings


class LiveRuntime:
    """
    Thin runtime wrapper over Gemini Live API (google-genai / Vertex AI).

    Responsibilities:
    - open/close a live session
    - send grounding context text
    - stream audio chunks in real time (VAD mode → auto multi-turn + interrupt)
    - emit normalised server events to callers via an asyncio.Queue
    """

    def __init__(self) -> None:
        settings = get_settings()
        self.settings = settings
        self.client = genai.Client(
            vertexai=True,
            project=settings.google_cloud_project,
            location=settings.google_cloud_location,
        )

        self.session = None
        self._receiver_task: Optional[asyncio.Task] = None
        self._event_queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue()
        self._session_cm = None

    async def connect(self, system_instruction: str | None = None) -> None:
        if self.session is not None:
            return

        config: dict[str, Any] = {
            "response_modalities": ["AUDIO"],
            "input_audio_transcription": {},
            "output_audio_transcription": {},
            # Explicitly enable server-side VAD so the model interrupts itself
            # when user speech is detected mid-response.
            "realtime_input_config": {
                "automatic_activity_detection": {},
            },
        }
        if system_instruction:
            config["system_instruction"] = system_instruction

        self._session_cm = self.client.aio.live.connect(
            model=self.settings.live_notebook_agent_model,
            config=config,
        )
        self.session = await self._session_cm.__aenter__()

        # Start receiver immediately so no early events are missed.
        await self._ensure_receiver_started()

    async def _ensure_receiver_started(self) -> None:
        if self._receiver_task is None or self._receiver_task.done():
            self._receiver_task = asyncio.create_task(self._receiver_loop())

    async def close(self) -> None:
        if self._receiver_task:
            self._receiver_task.cancel()
            try:
                await self._receiver_task
            except asyncio.CancelledError:
                pass
            self._receiver_task = None

        if self._session_cm is not None:
            try:
                await self._session_cm.__aexit__(None, None, None)
            finally:
                self._session_cm = None
                self.session = None

    async def send_turn_context(self, grounded_prompt: str) -> None:
        """
        Send retrieved grounding context as pre-turn text (turn_complete=False
        so the audio turn can follow immediately).
        """
        if self.session is None:
            raise RuntimeError("Live session is not connected")

        await self.session.send_client_content(
            turns=[
                {
                    "role": "user",
                    "parts": [
                        {
                            "text": (
                                "Reference context for the next spoken turn.\n"
                                "Use this to ground your answer, then listen to the user's voice input.\n\n"
                                f"{grounded_prompt}"
                            )
                        }
                    ],
                }
            ],
            turn_complete=False,
        )

    async def send_audio_chunk(
        self,
        audio_bytes: bytes,
        mime_type: str = "audio/pcm;rate=16000",
    ) -> None:
        if self.session is None:
            raise RuntimeError("Live session is not connected")

        await self.session.send_realtime_input(
            audio=types.Blob(
                data=audio_bytes,
                mime_type=mime_type,
            )
        )

    async def end_audio_stream(self) -> None:
        if self.session is None:
            raise RuntimeError("Live session is not connected")

        await self.session.send_realtime_input(audio_stream_end=True)

    async def receive_events(self) -> AsyncIterator[dict[str, Any]]:
        while True:
            event = await self._event_queue.get()
            yield event

    # ── Internal receiver ────────────────────────────────────────────────────

    async def _receiver_loop(self) -> None:
        assert self.session is not None

        try:
            # Outer loop keeps the receiver alive across multiple VAD turns.
            # session.receive() is a one-shot generator that ends at turn_complete;
            # we must call it again for each subsequent turn.
            while True:
                async for message in self.session.receive():
                    server_content = getattr(message, "server_content", None)

                    if server_content:
                        # ── Model interrupted (VAD detected user speech) ──────
                        interrupted = getattr(server_content, "interrupted", False)
                        if interrupted:
                            await self._event_queue.put({"type": "assistant_interrupted"})

                        # ── Model audio / text parts ──────────────────────────
                        model_turn = getattr(server_content, "model_turn", None)
                        if model_turn and getattr(model_turn, "parts", None):
                            for part in model_turn.parts:
                                inline_data = getattr(part, "inline_data", None)
                                text = getattr(part, "text", None)

                                if text:
                                    await self._event_queue.put(
                                        {"type": "assistant_text", "text": text}
                                    )

                                if inline_data and getattr(inline_data, "data", None):
                                    encoded = base64.b64encode(inline_data.data).decode("utf-8")
                                    await self._event_queue.put(
                                        {
                                            "type": "assistant_audio_chunk",
                                            "mime_type": getattr(
                                                inline_data, "mime_type", "audio/pcm"
                                            ),
                                            "data": encoded,
                                        }
                                    )

                        # ── Turn complete ─────────────────────────────────────
                        if getattr(server_content, "turn_complete", False):
                            await self._event_queue.put({"type": "turn_complete"})

                        # ── Input transcription (user speech → text) ──────────
                        # Check inside server_content first, then top-level fallback
                        input_tx = getattr(server_content, "input_transcription", None)
                        if input_tx:
                            text = (
                                getattr(input_tx, "text", None)
                                or getattr(input_tx, "transcript", None)
                            )
                            if text:
                                await self._event_queue.put(
                                    {"type": "user_transcript", "text": text}
                                )

                        # ── Output transcription (model speech → text) ─────────
                        output_tx = getattr(server_content, "output_transcription", None)
                        if output_tx:
                            text = (
                                getattr(output_tx, "text", None)
                                or getattr(output_tx, "transcript", None)
                            )
                            if text:
                                await self._event_queue.put(
                                    {"type": "assistant_transcript", "text": text}
                                )

                    # ── Top-level transcription fallback (older SDK versions) ──
                    top_input_tx = getattr(message, "input_transcription", None)
                    if top_input_tx and not server_content:
                        text = (
                            getattr(top_input_tx, "text", None)
                            or getattr(top_input_tx, "transcript", None)
                        )
                        if text:
                            await self._event_queue.put(
                                {"type": "user_transcript", "text": text}
                            )

                    top_output_tx = getattr(message, "output_transcription", None)
                    if top_output_tx and not server_content:
                        text = (
                            getattr(top_output_tx, "text", None)
                            or getattr(top_output_tx, "transcript", None)
                        )
                        if text:
                            await self._event_queue.put(
                                {"type": "assistant_transcript", "text": text}
                            )

        except asyncio.CancelledError:
            raise  # Let task cancellation propagate; finally still emits runtime_closed
        except ConnectionClosedOK:
            pass  # 1000 normal close
        except ConnectionClosedError:
            pass  # 1006 abnormal closure (network drop / session timeout) — not an error
        except genai_errors.APIError as exc:
            # Gemini Live closes gracefully (1000), idle-silenced (1007), or
            # drops the connection (1006 surfaced as APIError in some SDK versions).
            status = getattr(exc, "status_code", None)
            msg = str(exc)
            if (
                status in (1000, 1006, 1007)
                or msg.startswith("1000")
                or msg.startswith("1006")
                or msg.startswith("1007")
            ):
                pass
            else:
                await self._event_queue.put(
                    {
                        "type": "runtime_error",
                        "message": f"{type(exc).__name__}: {exc}",
                    }
                )
        except Exception as exc:
            await self._event_queue.put(
                {
                    "type": "runtime_error",
                    "message": f"{type(exc).__name__}: {exc}",
                }
            )
        finally:
            await self._event_queue.put({"type": "runtime_closed"})
