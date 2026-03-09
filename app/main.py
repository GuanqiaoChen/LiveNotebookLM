"""
LiveNotebookLM FastAPI application.

WebSocket endpoint for real-time voice interaction via ADK Gemini Live API Toolkit.
"""
import os
from dotenv import load_dotenv
from fastapi import FastAPI

load_dotenv()

REQUIRED_ENV_VARS = [
    "GOOGLE_CLOUD_PROJECT",
    "GOOGLE_CLOUD_LOCATION",
    "GOOGLE_GENAI_USE_VERTEXAI",
    "LIVE_NOTEBOOK_AGENT_MODEL",
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
    description="NotebookLM with real-time voice interaction",
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
        "websocket": "/ws/{user_id}/{session_id}",
    }


@app.get("/health")
async def health():
    """Liveness probe for Cloud Run."""
    return {"status": "healthy"}


# TODO: Implement WebSocket endpoint for bidirectional streaming
# @app.websocket("/ws/{user_id}/{session_id}")
# async def websocket_endpoint(websocket: WebSocket, user_id: str, session_id: str):
#     """
#     WebSocket for real-time voice/text interaction.
#     Uses LiveRequestQueue + runner.run_live() per ADK bidi-streaming pattern.
#     """
#     pass
