from __future__ import annotations

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from app.schemas import (
    CreateSessionRequest,
    CreateSessionResponse,
    SessionDetail,
    SessionMetadata,
)
from app.session_store import SessionStore
from app.source_store import SourceStore


router = APIRouter(prefix="/sessions", tags=["sessions"])


@router.post("", response_model=CreateSessionResponse)
async def create_session(payload: CreateSessionRequest) -> CreateSessionResponse:
    store = SessionStore()
    metadata = store.create_session(payload.title)

    return CreateSessionResponse(
        session_id=metadata.session_id,
        title=metadata.title,
        created_at=metadata.created_at,
    )


@router.get("", response_model=list[SessionMetadata])
async def list_sessions() -> list[SessionMetadata]:
    store = SessionStore()
    return store.list_sessions()


@router.get("/{session_id}", response_model=SessionDetail)
async def get_session(session_id: str) -> SessionDetail:
    session_store = SessionStore()
    source_store = SourceStore()

    try:
        detail = session_store.get_session_detail(session_id)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

    detail.sources = source_store.list_sources(session_id)
    return detail


class UpdateSessionTitleRequest(BaseModel):
    title: str


@router.patch("/{session_id}", response_model=SessionMetadata)
async def update_session_title(session_id: str, payload: UpdateSessionTitleRequest) -> SessionMetadata:
    store = SessionStore()
    try:
        return store.update_session_title(session_id, payload.title)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.delete("/{session_id}")
async def delete_session(session_id: str) -> dict:
    session_store = SessionStore()

    try:
        session_store.delete_session(session_id)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

    return {
        "status": "ok",
        "message": f"Session deleted: {session_id}",
    }