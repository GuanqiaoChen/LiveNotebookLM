from __future__ import annotations

from datetime import datetime
from typing import Literal, Optional

from pydantic import BaseModel, Field


SourceKind = Literal["uploaded_file", "web_result"]
ProcessingStatus = Literal["uploaded", "processed", "indexed", "failed"]
MessageRole = Literal["system", "user", "assistant"]


class Citation(BaseModel):
    source_id: str
    source_name: str
    page: Optional[int] = None
    section: Optional[str] = None
    snippet: Optional[str] = None
    url: Optional[str] = None


class MessageRecord(BaseModel):
    turn_id: str
    session_id: str
    role: MessageRole
    content: str = ""
    timestamp: datetime
    interrupted: bool = False
    citations: list[Citation] = Field(default_factory=list)


class SessionMetadata(BaseModel):
    session_id: str
    title: str
    created_at: datetime
    updated_at: datetime
    source_ids: list[str] = Field(default_factory=list)
    source_count: int = 0
    message_count: int = 0
    is_active: bool = True
    ended_at: Optional[datetime] = None
    voice: str = "Aoede"


class SourceMetadata(BaseModel):
    source_id: str
    session_id: str
    kind: SourceKind
    display_name: str
    original_filename: Optional[str] = None
    mime_type: Optional[str] = None
    gcs_uri: Optional[str] = None
    source_url: Optional[str] = None
    uploaded_at: datetime
    processing_status: ProcessingStatus = "uploaded"
    chunk_count: int = 0


class SessionDetail(BaseModel):
    metadata: SessionMetadata
    sources: list[SourceMetadata] = Field(default_factory=list)
    messages: list[MessageRecord] = Field(default_factory=list)


class CreateSessionRequest(BaseModel):
    title: Optional[str] = None
    voice: Optional[str] = None


class CreateSessionResponse(BaseModel):
    session_id: str
    title: str
    created_at: datetime


class WebSearchResultItem(BaseModel):
    """A single web search result candidate (not yet saved as a source)."""
    title: str
    url: str
    snippet: str


class WebSearchRequest(BaseModel):
    query: str
    pending_count: int = 0  # checked results not yet added to session


class WebSearchResponse(BaseModel):
    results: list[WebSearchResultItem]
    remaining_capacity: int  # how many more sources this session can accept


class AddWebSourcesRequest(BaseModel):
    """Commit checked web-search results as actual session sources."""
    results: list[WebSearchResultItem]


class AddWebSourcesResponse(BaseModel):
    added: list[SourceMetadata]
    remaining_capacity: int


# Backward-compat alias
WebSearchResult = WebSearchResultItem


class RecapData(BaseModel):
    session_id: str
    topic: str = ""
    key_insights: list[str] = Field(default_factory=list)
    sources_referenced: list[str] = Field(default_factory=list)
    open_questions: list[str] = Field(default_factory=list)
    next_steps: list[str] = Field(default_factory=list)
    generated_at: Optional[datetime] = None


class FollowUpResponse(BaseModel):
    session_id: str
    suggestions: list[str] = Field(default_factory=list)
    generated_at: Optional[datetime] = None