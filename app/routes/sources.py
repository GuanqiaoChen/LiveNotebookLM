from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from fastapi import APIRouter, File, HTTPException, UploadFile

from config import get_settings
from gcs_store import upload_bytes
from schemas import SourceMetadata
from session_store import SessionStore
from source_store import SourceStore


router = APIRouter(prefix="/sessions/{session_id}/sources", tags=["sources"])


def _safe_filename(filename: str) -> str:
    return Path(filename).name.replace(" ", "_")


@router.post("/upload", response_model=SourceMetadata)
async def upload_source(
    session_id: str,
    file: UploadFile = File(...),
) -> SourceMetadata:
    session_store = SessionStore()
    source_store = SourceStore()
    settings = get_settings()

    try:
        session_store.get_session_metadata(session_id)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

    remaining = source_store.remaining_capacity(session_id)
    if remaining <= 0:
        raise HTTPException(
            status_code=400,
            detail=f"Source limit reached. Max per session is {settings.max_sources_per_session}.",
        )

    if not file.filename:
        raise HTTPException(status_code=400, detail="Uploaded file must have a filename.")

    try:
        content = await file.read()
        if not content:
            raise HTTPException(status_code=400, detail="Uploaded file is empty.")

        safe_name = _safe_filename(file.filename)
        ts = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
        gcs_path = f"sessions/{session_id}/sources/{ts}-{safe_name}"

        gcs_uri = upload_bytes(
            path=gcs_path,
            data=content,
            content_type=file.content_type or "application/octet-stream",
        )

        source = source_store.add_uploaded_source(
            session_id=session_id,
            display_name=safe_name,
            original_filename=file.filename,
            mime_type=file.content_type or "application/octet-stream",
            gcs_uri=gcs_uri,
        )
        return source

    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("", response_model=list[SourceMetadata])
async def list_sources(session_id: str) -> list[SourceMetadata]:
    session_store = SessionStore()
    source_store = SourceStore()

    try:
        session_store.get_session_metadata(session_id)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

    return source_store.list_sources(session_id)


@router.delete("/{source_id}")
async def delete_source(session_id: str, source_id: str) -> dict:
    session_store = SessionStore()
    source_store = SourceStore()

    try:
        session_store.get_session_metadata(session_id)
        source_store.delete_source(session_id, source_id)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

    return {
        "status": "ok",
        "message": f"Source deleted: {source_id}",
    }