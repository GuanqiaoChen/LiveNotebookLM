from __future__ import annotations

import asyncio
import base64
from typing import Any, AsyncIterator, Optional

from google import genai
from google.genai import types
from google.genai import errors as genai_errors
from websockets.exceptions import ConnectionClosedOK

from app.config import get_settings


class LiveRuntime:
    """
    Thin runtime wrapper over Gemini Live API.

    Responsibilities:
    - open/close a live session
    - send grounding context text
    - stream audio chunks in real time
    - emit normalized server events
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
        }
        if system_instruction:
            config["system_instruction"] = system_instruction

        self._session_cm = self.client.aio.live.connect(
            model=self.settings.live_notebook_agent_model,
            config=config,
        )
        self.session = await self._session_cm.__aenter__()

    async def _ensure_receiver_started(self) -> None:
        if self._receiver_task is None:
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
        Send retrieved grounding context as pre-turn text.
        This does NOT finish the turn. Audio continues after this.
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

        await self._ensure_receiver_started()

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

    async def _receiver_loop(self) -> None:
        assert self.session is not None

        try:
            async for message in self.session.receive():
                server_content = getattr(message, "server_content", None)
                if server_content:
                    model_turn = getattr(server_content, "model_turn", None)
                    turn_complete = getattr(server_content, "turn_complete", None)

                    if model_turn and getattr(model_turn, "parts", None):
                        for part in model_turn.parts:
                            inline_data = getattr(part, "inline_data", None)
                            text = getattr(part, "text", None)

                            if text:
                                await self._event_queue.put(
                                    {
                                        "type": "assistant_text",
                                        "text": text,
                                    }
                                )

                            if inline_data and getattr(inline_data, "data", None):
                                encoded = base64.b64encode(inline_data.data).decode("utf-8")
                                await self._event_queue.put(
                                    {
                                        "type": "assistant_audio_chunk",
                                        "mime_type": getattr(inline_data, "mime_type", "audio/pcm"),
                                        "data": encoded,
                                    }
                                )

                    if turn_complete:
                        await self._event_queue.put({"type": "turn_complete"})

                input_tx = getattr(message, "input_transcription", None)
                if input_tx:
                    text = getattr(input_tx, "text", None) or getattr(input_tx, "transcript", None)
                    if text:
                        await self._event_queue.put(
                            {
                                "type": "user_transcript",
                                "text": text,
                            }
                        )

                output_tx = getattr(message, "output_transcription", None)
                if output_tx:
                    text = getattr(output_tx, "text", None) or getattr(output_tx, "transcript", None)
                    if text:
                        await self._event_queue.put(
                            {
                                "type": "assistant_transcript",
                                "text": text,
                            }
                        )
        except ConnectionClosedOK:
            # 正常关闭，不当成错误
            pass
        except genai_errors.APIError as exc:
            # Gemini Live sometimes surfaces normal close as APIError 1000
            if getattr(exc, "status_code", None) == 1000 or str(exc).startswith("1000"):
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