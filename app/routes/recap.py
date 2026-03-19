from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException

from app.routes.deps import get_client_id
from app.schemas import FollowUpResponse, RecapData
from app.session_store import SessionStore
from app.source_store import SourceStore
from app.live_notebook_agent.sub_agents.recap_manager import (
    generate_follow_up_suggestions,
    generate_recap_data,
    load_recap_data,
    save_recap_data,
)


router = APIRouter(prefix="/sessions/{session_id}/recap", tags=["recap"])


@router.post("/generate", response_model=RecapData)
async def generate_recap(
    session_id: str,
    client_id: str = Depends(get_client_id),
) -> RecapData:
    session_store = SessionStore(client_id=client_id)
    source_store = SourceStore(client_id=client_id)

    try:
        session_store.get_session_metadata(session_id)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

    messages = [msg.model_dump(mode="json") for msg in session_store.get_messages(session_id)]
    sources = [src.model_dump(mode="json") for src in source_store.list_sources(session_id)]

    if not messages:
        raise HTTPException(status_code=400, detail="Cannot generate recap for an empty session.")

    try:
        recap = generate_recap_data(
            session_id=session_id,
            messages=messages,
            sources=sources,
        )
        save_recap_data(session_id, recap, client_id=client_id)
        return recap
    except Exception as exc:
        raise HTTPException(
            status_code=500,
            detail=f"Recap generation failed: {type(exc).__name__}: {exc}",
        ) from exc


@router.post("/follow-up", response_model=FollowUpResponse)
async def get_follow_up_suggestions(
    session_id: str,
    client_id: str = Depends(get_client_id),
) -> FollowUpResponse:
    session_store = SessionStore(client_id=client_id)

    try:
        session_store.get_session_metadata(session_id)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

    messages = [msg.model_dump(mode="json") for msg in session_store.get_messages(session_id)]
    if not messages:
        return FollowUpResponse(session_id=session_id, suggestions=[])

    try:
        return generate_follow_up_suggestions(session_id=session_id, messages=messages)
    except Exception as exc:
        raise HTTPException(
            status_code=500,
            detail=f"Follow-up generation failed: {type(exc).__name__}: {exc}",
        ) from exc


@router.get("", response_model=RecapData)
async def get_recap(
    session_id: str,
    client_id: str = Depends(get_client_id),
) -> RecapData:
    session_store = SessionStore(client_id=client_id)

    try:
        session_store.get_session_metadata(session_id)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

    recap = load_recap_data(session_id, client_id=client_id)
    if recap is None:
        raise HTTPException(status_code=404, detail="Recap not found for this session.")

    return recap
