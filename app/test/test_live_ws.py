import asyncio
import base64
import json
import websockets


SESSION_ID = "ad2ce6a5-6281-438a-8e2e-378a512f9853"
WS_URL = f"ws://127.0.0.1:8080/ws/live/{SESSION_ID}"


async def recv_with_timeout(ws, timeout=10):
    msg = await asyncio.wait_for(ws.recv(), timeout=timeout)
    print(msg)
    return msg


async def main():
    async with websockets.connect(
        WS_URL,
        max_size=None,
        open_timeout=20,
        ping_interval=20,
        ping_timeout=20,
    ) as ws:
        # 1. Connected
        await recv_with_timeout(ws, timeout=10)

        # 2. Live runtime
        await ws.send(json.dumps({"type": "session_start"}))
        await recv_with_timeout(ws, timeout=20)  # runtime_connecting
        await recv_with_timeout(ws, timeout=20)  # runtime_ready

        # 3. One turn
        await ws.send(
            json.dumps(
                {
                    "type": "start_turn",
                    "text_hint": "What does this project do?",
                }
            )
        )

        # Receive source_cues
        await recv_with_timeout(ws, timeout=10)

        # 4. Send a short fake PCM chunk for testing
        fake_audio = b"\x00\x00" * 320
        await ws.send(
            json.dumps(
                {
                    "type": "audio_chunk",
                    "mime_type": "audio/pcm;rate=16000",
                    "data": base64.b64encode(fake_audio).decode("utf-8"),
                }
            )
        )

        # 5. End audio input
        await ws.send(json.dumps({"type": "audio_stream_end"}))

        # 6. Front end provides transcript for local text-only persistence
        await ws.send(
            json.dumps(
                {
                    "type": "commit_user_text",
                    "text": "What does this project do?",
                }
            )
        )

        # 7. Look at subsequent events
        for _ in range(10):
            try:
                msg = await recv_with_timeout(ws, timeout=8)
                if '"type":"session_closed"' in msg or '"type":"turn_complete"' in msg:
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