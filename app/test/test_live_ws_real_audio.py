import asyncio
import base64
import io
import json
import wave

import websockets


SESSION_ID = "ad2ce6a5-6281-438a-8e2e-378a512f9853"
WS_URL = f"ws://127.0.0.1:8080/ws/live/{SESSION_ID}"
AUDIO_FILE = r"D://projects//LiveNotebookLM//app//test//sample_voice.wav"

CHUNK_MS = 100  # Each chunk will be 100ms of audio


def load_wav_pcm_chunks(path: str, chunk_ms: int = 100) -> list[bytes]:
    with wave.open(path, "rb") as wf:
        channels = wf.getnchannels()
        sampwidth = wf.getsampwidth()
        framerate = wf.getframerate()

        if channels != 1:
            raise ValueError(f"WAV must be mono. Got channels={channels}")
        if sampwidth != 2:
            raise ValueError(f"WAV must be 16-bit PCM. Got sampwidth={sampwidth}")
        if framerate != 16000:
            raise ValueError(f"WAV must be 16kHz. Got framerate={framerate}")

        frames_per_chunk = int(framerate * chunk_ms / 1000)
        chunks = []

        while True:
            data = wf.readframes(frames_per_chunk)
            if not data:
                break
            chunks.append(data)

        return chunks


async def recv_with_timeout(ws, timeout=10):
    msg = await asyncio.wait_for(ws.recv(), timeout=timeout)
    print(msg)
    return msg


async def main():
    chunks = load_wav_pcm_chunks(AUDIO_FILE, CHUNK_MS)

    async with websockets.connect(
        WS_URL,
        max_size=None,
        open_timeout=20,
        ping_interval=20,
        ping_timeout=20,
    ) as ws:
        # 1. Connect to websocket
        await recv_with_timeout(ws, timeout=10)  # connected

        # 2. Initialize live runtime
        await ws.send(json.dumps({"type": "session_start"}))
        await recv_with_timeout(ws, timeout=20)  # runtime_connecting
        await recv_with_timeout(ws, timeout=20)  # runtime_ready

        # 3. Start a turn
        await ws.send(
            json.dumps(
                {
                    "type": "start_turn",
                    "text_hint": "What does this project do?",
                }
            )
        )

        # Receive source_cues
        try:
            await recv_with_timeout(ws, timeout=10)
        except asyncio.TimeoutError:
            pass

        # 4. Send audio chunks in real-time
        for chunk in chunks:
            await ws.send(
                json.dumps(
                    {
                        "type": "audio_chunk",
                        "mime_type": "audio/pcm;rate=16000",
                        "data": base64.b64encode(chunk).decode("utf-8"),
                    }
                )
            )
            await asyncio.sleep(CHUNK_MS / 1000.0)

        # 5. End audio input
        await ws.send(json.dumps({"type": "audio_stream_end"}))

        # 6. Save the final user text
        await ws.send(
            json.dumps(
                {
                    "type": "commit_user_text",
                    "text": "What does this project do?",
                }
            )
        )

        # 7. Look at the model's response
        for _ in range(30):
            try:
                msg = await recv_with_timeout(ws, timeout=8)
                if '"type": "turn_complete"' in msg or '"type":"turn_complete"' in msg:
                    break
            except asyncio.TimeoutError:
                break
            except websockets.exceptions.ConnectionClosedOK:
                print("WebSocket closed normally.")
                break
            except websockets.exceptions.ConnectionClosedError as exc:
                print(f"WebSocket closed: {exc}")
                break

        await ws.send(json.dumps({"type": "close_session"}))


if __name__ == "__main__":
    asyncio.run(main())