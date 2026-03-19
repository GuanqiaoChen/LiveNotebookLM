import logging
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from dotenv import load_dotenv
from fastapi import FastAPI, WebSocket
from google import genai

from app.config import get_settings
from app.gcs_store import upload_text, upload_bytes
from app.routes import sessions_router, sources_router, recap_router, backup_router, voices_router
from app.live_notebook_agent.agent import AGENT_REGISTRY
from app.ws_handlers import handle_live_websocket

from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pathlib import Path

logger = logging.getLogger(__name__)

load_dotenv()
get_settings()


@asynccontextmanager
async def lifespan(_app: FastAPI):
    # On cold start, restore any GCS-backed sessions not present locally.
    # This ensures data survives container restarts (Cloud Run, etc.).
    # Restore all users' sessions from GCS (client_id-namespaced).
    from app.gcs_backup import GCSBackupService
    try:
        result = await GCSBackupService().restore_all_users(overwrite=False)
        logger.info(
            "GCS startup restore — restored: %d, skipped: %d, total: %d",
            result["restored"], result["skipped"], result["total"],
        )
    except Exception as exc:
        logger.warning("GCS startup restore skipped (non-fatal): %s", exc)
    yield  # app runs here


app = FastAPI(
    title="LiveNotebookLM",
    version="0.1.0",
    description="NotebookLM with real-time voice interaction via Gemini Live API",
    lifespan=lifespan,
)

BASE_DIR = Path(__file__).resolve().parent
STATIC_DIR = BASE_DIR / "static"

app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

app.include_router(sessions_router)
app.include_router(sources_router)
app.include_router(recap_router)
app.include_router(backup_router)
app.include_router(voices_router)


@app.get("/")
async def root():
    settings = get_settings()
    return {
        "service": "LiveNotebookLM",
        "status": "ok",
        "platform": "vertex-ai",
        "model": settings.live_notebook_agent_model,
        "agents": list(AGENT_REGISTRY.keys()),
    }


@app.get("/health")
async def health():
    return {"status": "healthy"}


@app.post("/debug/upload-smoke")
async def upload_smoke():
    settings = get_settings()
    ts = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    path = f"debug/smoke-{ts}.txt"

    uri = upload_text(
        path,
        "hello from LiveNotebookLM upload smoke test",
    )

    return {
        "status": "ok",
        "bucket": settings.gcs_bucket,
        "uri": uri,
    }


@app.post("/debug/live-smoke")
async def live_smoke():
    try:
        settings = get_settings()

        client = genai.Client(
            vertexai=True,
            project=settings.google_cloud_project,
            location=settings.google_cloud_location,
        )

        config = {
            "response_modalities": ["AUDIO"],
        }

        audio_bytes = None

        async with client.aio.live.connect(
            model=settings.live_notebook_agent_model,
            config=config,
        ) as session:
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

        ts = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
        path = f"debug/live-smoke-{ts}.bin"
        uri = upload_bytes(path, audio_bytes, "application/octet-stream")

        return {
            "status": "ok",
            "bucket": settings.gcs_bucket,
            "uri": uri,
            "bytes": len(audio_bytes),
            "model": settings.live_notebook_agent_model,
        }

    except Exception as exc:
        return {
            "status": "error",
            "error_type": type(exc).__name__,
            "message": str(exc),
        }


@app.websocket("/ws/live/{session_id}")
async def live_ws(websocket: WebSocket, session_id: str, client_id: str = "default"):
    """
    WebSocket endpoint for Gemini Live.
    client_id is passed as a query parameter (?client_id=…) since WebSocket
    upgrade requests cannot carry custom headers in most browsers.
    """
    await handle_live_websocket(websocket, session_id, client_id)


@app.websocket("/ws/ping")
async def ws_ping(websocket: WebSocket):
    await websocket.accept()
    await websocket.send_json({"type": "pong"})
    await websocket.close()


@app.get("/ui")
async def ui():
    return FileResponse(STATIC_DIR / "index.html")


@app.get("/restore")
async def restore_page():
    return FileResponse(STATIC_DIR / "restore.html")
