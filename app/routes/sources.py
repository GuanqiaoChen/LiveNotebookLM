from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from fastapi import APIRouter, File, HTTPException, UploadFile

from app.config import get_settings
from app.gcs_backup import schedule_backup
from app.gcs_store import upload_bytes
from app.schemas import (
    AddWebSourcesRequest,
    AddWebSourcesResponse,
    SourceMetadata,
    WebSearchRequest,
    WebSearchResponse,
    WebSearchResultItem,
)
from app.session_store import SessionStore
from app.source_processor import SourceProcessor
from app.source_store import SourceStore
from app.live_notebook_agent.sub_agents.retriever import Retriever


router = APIRouter(prefix="/sessions/{session_id}/sources", tags=["sources"])


def _safe_filename(filename: str) -> str:
    return Path(filename).name.replace(" ", "_")


# ── File upload ───────────────────────────────────────────────────────────────

@router.post("/upload", response_model=SourceMetadata)
async def upload_source(
    session_id: str,
    file: UploadFile = File(...),
) -> SourceMetadata:
    session_store = SessionStore()
    source_store = SourceStore()
    source_processor = SourceProcessor()
    retriever = Retriever()
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

        chunks = source_processor.process_uploaded_bytes(
            source=source,
            filename=file.filename,
            content=content,
        )

        if chunks and retriever.is_configured():
            retriever.index_chunks_with_vertex_embeddings(
                session_id=session_id,
                chunks=chunks,
            )

        source.processing_status = "indexed"
        source.chunk_count = len(chunks)
        source_store.update_source(source)

        schedule_backup(session_id)
        return source

    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        try:
            existing_sources = source_store.list_sources(session_id)
            if existing_sources:
                latest = existing_sources[-1]
                latest.processing_status = "failed"
                source_store.update_source(latest)
        except Exception:
            pass
        raise HTTPException(
            status_code=500,
            detail=f"Source processing/indexing failed: {type(exc).__name__}: {exc}",
        ) from exc


# ── List / delete ─────────────────────────────────────────────────────────────

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

    return {"status": "ok", "message": f"Source deleted: {source_id}"}


# ── Web search (preview — does NOT add to session) ────────────────────────────

@router.post("/web-search", response_model=WebSearchResponse)
async def web_search_sources(
    session_id: str,
    request: WebSearchRequest,
) -> WebSearchResponse:
    """
    Run the ADK web_search_agent for *query* and return candidate results.
    Results are NOT saved — the user selects which ones to add via /add-web.

    max_results = min(10, remaining_capacity - pending_count)
    where pending_count = checked results from a previous search not yet added.
    """
    session_store = SessionStore()
    source_store = SourceStore()

    try:
        session_store.get_session_metadata(session_id)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

    remaining = source_store.remaining_capacity(session_id)
    # Account for results the user has already checked in the UI (pending_count)
    effective_capacity = remaining - max(0, request.pending_count)
    max_results = min(10, effective_capacity)

    if max_results <= 0:
        raise HTTPException(
            status_code=400,
            detail=(
                f"No capacity for new search results. "
                f"Session has {10 - remaining}/10 sources and "
                f"{request.pending_count} pending selection(s)."
            ),
        )

    from app.web_search_service import search_web  # lazy import avoids cold-start delay

    raw = await search_web(request.query, max_results=max_results)
    results = [WebSearchResultItem(**r) for r in raw]

    return WebSearchResponse(results=results, remaining_capacity=remaining)


# ── Add selected web results as sources ───────────────────────────────────────

@router.post("/add-web", response_model=AddWebSourcesResponse)
async def add_web_sources(
    session_id: str,
    request: AddWebSourcesRequest,
) -> AddWebSourcesResponse:
    """
    Save the user-selected web search results as session sources.
    Chunks each result's snippet text and indexes it for RAG.
    """
    session_store = SessionStore()
    source_store = SourceStore()
    source_processor = SourceProcessor()
    retriever = Retriever()

    try:
        session_store.get_session_metadata(session_id)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

    if not request.results:
        return AddWebSourcesResponse(
            added=[], remaining_capacity=source_store.remaining_capacity(session_id)
        )

    remaining = source_store.remaining_capacity(session_id)
    if len(request.results) > remaining:
        raise HTTPException(
            status_code=400,
            detail=(
                f"Cannot add {len(request.results)} sources — "
                f"only {remaining} slot(s) remaining (max 10 per session)."
            ),
        )

    added: list[SourceMetadata] = []

    for item in request.results:
        try:
            source = source_store.add_web_source(
                session_id=session_id,
                title=item.title or item.url,
                url=item.url,
            )

            # Build a small document from the snippet for chunking / RAG
            web_text = f"Title: {item.title}\nURL: {item.url}\n\n{item.snippet}"
            chunks = source_processor.process_source(
                source=source,
                web_text=web_text,
            )

            if chunks:
                if retriever.is_configured():
                    retriever.index_chunks_with_vertex_embeddings(
                        session_id=session_id,
                        chunks=chunks,
                    )
                source.processing_status = "indexed"
                source.chunk_count = len(chunks)
            else:
                source.processing_status = "processed"

            source_store.update_source(source)
            added.append(source)

        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    schedule_backup(session_id)
    return AddWebSourcesResponse(
        added=added,
        remaining_capacity=source_store.remaining_capacity(session_id),
    )
