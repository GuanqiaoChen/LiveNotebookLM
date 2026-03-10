"""
LiveNotebookLM FastAPI application.

WebSocket endpoint for real-time voice interaction via ADK Gemini Live API Toolkit.
"""
import os
import datetime
from dotenv import load_dotenv
from fastapi import FastAPI
from google import genai

from gcs_store import upload_text, upload_bytes

load_dotenv()

REQUIRED_ENV_VARS = [
    "GOOGLE_CLOUD_PROJECT",
    "GOOGLE_CLOUD_LOCATION",
    "GOOGLE_GENAI_USE_VERTEXAI",
    "LIVE_NOTEBOOK_AGENT_MODEL",
    "GCS_BUCKET",
]

def validate_env():
    missing = [k for k in REQUIRED_ENV_VARS if not os.getenv(k)]
    if missing:
        raise RuntimeError(f"Missing required environment variables: {', '.join(missing)}")
    if os.getenv("GOOGLE_GENAI_USE_VERTEXAI", "").lower() != "true":
        raise RuntimeError("GOOGLE_GENAI_USE_VERTEXAI must be set to true")

validate_env()

from live_notebook_agent.agent import root_agent

# TODO: Initialize ADK Runner, SessionService for run_live()
# from google.adk.runners import Runner
# from google.adk.sessions import InMemorySessionService
# session_service = InMemorySessionService()
# runner = Runner(app_name="live-notebook-lm", agent=root_agent, session_service=session_service)

app = FastAPI(
    title="LiveNotebookLM",
    description="NotebookLM with real-time voice interaction via Gemini Live API",
    version="0.1.0",
)

# TODO: Mount static files when frontend is added
# static_path = os.path.join(os.path.dirname(__file__), "static")
# if os.path.exists(static_path):
#     app.mount("/static", StaticFiles(directory=static_path), name="static")


@app.get("/")
async def root():
    """Health check and API info."""
    return {
        "service": "LiveNotebookLM",
        "status": "ok",
        "platform": "vertex-ai",
        "model": os.getenv("LIVE_NOTEBOOK_AGENT_MODEL"),
        "agent": root_agent.name,
    }


@app.get("/health")
async def health():
    """Liveness probe for Cloud Run."""
    return {"status": "healthy"}


@app.post("/debug/upload-smoke")
async def upload_smoke():
    ts = datetime.datetime.utcnow().strftime("%Y%m%d-%H%M%S")
    path = f"debug/smoke-{ts}.txt"

    uri = upload_text(
        path,
        "hello from LiveNotebookLM upload smoke test",
    )

    return {
        "status": "ok",
        "bucket": os.environ["GCS_BUCKET"],
        "uri": uri,
    }


@app.post("/debug/live-smoke")
async def live_smoke():
    project = os.environ["GOOGLE_CLOUD_PROJECT"]
    location = os.environ["GOOGLE_CLOUD_LOCATION"]
    model = os.environ["LIVE_NOTEBOOK_AGENT_MODEL"]

    client = genai.Client(vertexai=True, project=project, location=location)

    config = {
        "response_modalities": ["AUDIO"],
    }

    audio_bytes = None

    async with client.aio.live.connect(model=model, config=config) as session:
        await session.send_client_content(
            turns=[
                {
                    "role": "user",
                    "parts": [{"text": "Please say hello briefly."}],
                }
            ],
            turn_complete=True,
        )

        async for message in session.receive():
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
                    break

            if audio_bytes:
                break

    if not audio_bytes:
        return {
            "status": "error",
            "message": "No audio bytes received from Live API",
        }

    ts = datetime.datetime.utcnow().strftime("%Y%m%d-%H%M%S")
    path = f"debug/live-smoke-{ts}.bin"
    uri = upload_bytes(path, audio_bytes, "application/octet-stream")

    return {
        "status": "ok",
        "bucket": os.environ["GCS_BUCKET"],
        "uri": uri,
        "bytes": len(audio_bytes),
        "model": model,
    }

# TODO: Implement WebSocket endpoint for bidirectional streaming
# @app.websocket("/ws/{user_id}/{session_id}")
# async def websocket_endpoint(websocket: WebSocket, user_id: str, session_id: str):
#     """
#     WebSocket for real-time voice/text interaction.
#     Uses LiveRequestQueue + runner.run_live() per ADK bidi-streaming pattern.
#     """
#     pass
