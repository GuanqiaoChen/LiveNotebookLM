from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from dotenv import load_dotenv

APP_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = APP_DIR.parent

load_dotenv(APP_DIR / ".env")


@dataclass(frozen=True)
class Settings:
    google_cloud_project: str
    google_cloud_location: str
    google_genai_use_vertexai: str
    live_notebook_agent_model: str
    gcs_bucket: str
    port: int
    sessions_dir: str
    max_sources_per_session: int
    pinecone_api_key: str | None
    pinecone_index_name: str | None
    pinecone_namespace_prefix: str


def get_settings() -> Settings:
    required = [
        "GOOGLE_CLOUD_PROJECT",
        "GOOGLE_CLOUD_LOCATION",
        "GOOGLE_GENAI_USE_VERTEXAI",
        "LIVE_NOTEBOOK_AGENT_MODEL",
        "GCS_BUCKET",
    ]
    missing = [key for key in required if not os.getenv(key)]
    if missing:
        raise RuntimeError(
            f"Missing required environment variables: {', '.join(missing)}"
        )

    if os.getenv("GOOGLE_GENAI_USE_VERTEXAI", "").lower() != "true":
        raise RuntimeError("GOOGLE_GENAI_USE_VERTEXAI must be set to true")

    sessions_dir = os.getenv("SESSIONS_DIR")
    if sessions_dir:
        sessions_dir_path = Path(sessions_dir).resolve()
    else:
        sessions_dir_path = PROJECT_ROOT / "sessions"

    return Settings(
        google_cloud_project=os.environ["GOOGLE_CLOUD_PROJECT"],
        google_cloud_location=os.environ["GOOGLE_CLOUD_LOCATION"],
        google_genai_use_vertexai=os.environ["GOOGLE_GENAI_USE_VERTEXAI"],
        live_notebook_agent_model=os.environ["LIVE_NOTEBOOK_AGENT_MODEL"],
        gcs_bucket=os.environ["GCS_BUCKET"],
        port=int(os.getenv("PORT", "8080")),
        sessions_dir=str(sessions_dir_path),
        max_sources_per_session=int(os.getenv("MAX_SOURCES_PER_SESSION", "10")),
        pinecone_api_key=os.getenv("PINECONE_API_KEY"),
        pinecone_index_name=os.getenv("PINECONE_INDEX_NAME"),
        pinecone_namespace_prefix=os.getenv(
            "PINECONE_NAMESPACE_PREFIX", "live-notebook-lm"
        ),
    )