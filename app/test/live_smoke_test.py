import os
import asyncio
from pathlib import Path

from dotenv import load_dotenv
from google import genai

load_dotenv(Path(__file__).resolve().parent / ".env")

required = [
    "GOOGLE_CLOUD_PROJECT",
    "GOOGLE_CLOUD_LOCATION",
    "GOOGLE_GENAI_USE_VERTEXAI",
    "LIVE_NOTEBOOK_AGENT_MODEL",
]

missing = [k for k in required if not os.getenv(k)]
if missing:
    raise RuntimeError(f"Missing required environment variables: {', '.join(missing)}")

if os.getenv("GOOGLE_GENAI_USE_VERTEXAI", "").lower() != "true":
    raise RuntimeError("GOOGLE_GENAI_USE_VERTEXAI must be set to true")

PROJECT = os.environ["GOOGLE_CLOUD_PROJECT"]
LOCATION = os.environ["GOOGLE_CLOUD_LOCATION"]
MODEL = os.environ["LIVE_NOTEBOOK_AGENT_MODEL"]

async def main():
    print("Using Vertex AI Live config:")
    print(f"  project = {PROJECT}")
    print(f"  location = {LOCATION}")
    print(f"  model = {MODEL}")

    client = genai.Client(vertexai=True, project=PROJECT, location=LOCATION)

    config = {
        "response_modalities": ["AUDIO"],
    }

    async with client.aio.live.connect(model=MODEL, config=config) as session:
        print("\nLive session established.")

        await session.send_client_content(
            turns=[{
                "role": "user",
                "parts": [{"text": "Please say hello briefly."}]
            }],
            turn_complete=True,
        )

        print("Prompt sent. Waiting for audio response...\n")

        got_audio = False

        async for message in session.receive():
            print("RECEIVED EVENT:", type(message).__name__)

            server_content = getattr(message, "server_content", None)
            if not server_content:
                continue

            model_turn = getattr(server_content, "model_turn", None)
            if not model_turn or not getattr(model_turn, "parts", None):
                continue

            for part in model_turn.parts:
                inline_data = getattr(part, "inline_data", None)
                if inline_data and getattr(inline_data, "data", None):
                    audio_bytes = inline_data.data
                    print(f"Received audio bytes: {len(audio_bytes)}")
                    got_audio = True
                    break

            if got_audio:
                print("\nLive API audio smoke test passed.")
                break

if __name__ == "__main__":
    asyncio.run(main())