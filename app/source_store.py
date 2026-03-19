from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from pathlib import Path

from app.config import get_settings
from app.schemas import SourceMetadata
from app.session_store import SessionStore


class SourceStore:
    def __init__(self, client_id: str = "default") -> None:
        settings = get_settings()
        self.base_dir = (Path(settings.sessions_dir) / client_id).resolve()
        self.base_dir.mkdir(parents=True, exist_ok=True)
        self.max_sources_per_session = settings.max_sources_per_session
        self.session_store = SessionStore(client_id=client_id)

    def _sources_path(self, session_id: str) -> Path:
        session_dir = self.base_dir / session_id
        session_dir.mkdir(parents=True, exist_ok=True)
        return session_dir / "sources.json"

    def list_sources(self, session_id: str) -> list[SourceMetadata]:
        path = self._sources_path(session_id)
        if not path.exists():
            return []

        data = self._read_json(path)
        return [SourceMetadata.model_validate(item) for item in data]

    def add_uploaded_source(
        self,
        session_id: str,
        display_name: str,
        original_filename: str,
        mime_type: str,
        gcs_uri: str,
    ) -> SourceMetadata:
        sources = self.list_sources(session_id)
        self._ensure_capacity(len(sources), additional=1)

        source = SourceMetadata(
            source_id=str(uuid.uuid4()),
            session_id=session_id,
            kind="uploaded_file",
            display_name=display_name,
            original_filename=original_filename,
            mime_type=mime_type,
            gcs_uri=gcs_uri,
            source_url=None,
            uploaded_at=datetime.now(timezone.utc),
            processing_status="uploaded",
            chunk_count=0,
        )

        sources.append(source)
        self._persist_sources(session_id, sources)
        self._sync_session_metadata(session_id, sources)
        return source

    def add_web_source(
        self,
        session_id: str,
        title: str,
        url: str,
    ) -> SourceMetadata:
        sources = self.list_sources(session_id)
        self._ensure_capacity(len(sources), additional=1)

        source = SourceMetadata(
            source_id=str(uuid.uuid4()),
            session_id=session_id,
            kind="web_result",
            display_name=title,
            original_filename=None,
            mime_type="text/html",
            gcs_uri=None,
            source_url=url,
            uploaded_at=datetime.now(timezone.utc),
            processing_status="uploaded",
            chunk_count=0,
        )

        sources.append(source)
        self._persist_sources(session_id, sources)
        self._sync_session_metadata(session_id, sources)
        return source

    def get_source(self, session_id: str, source_id: str) -> SourceMetadata:
        for source in self.list_sources(session_id):
            if source.source_id == source_id:
                return source
        raise FileNotFoundError(f"Source not found: {source_id}")

    def update_source(self, source: SourceMetadata) -> None:
        sources = self.list_sources(source.session_id)
        updated: list[SourceMetadata] = []
        found = False

        for item in sources:
            if item.source_id == source.source_id:
                updated.append(source)
                found = True
            else:
                updated.append(item)

        if not found:
            raise FileNotFoundError(f"Source not found: {source.source_id}")

        self._persist_sources(source.session_id, updated)
        self._sync_session_metadata(source.session_id, updated)

    def delete_source(self, session_id: str, source_id: str) -> None:
        sources = self.list_sources(session_id)
        filtered = [s for s in sources if s.source_id != source_id]

        if len(filtered) == len(sources):
            raise FileNotFoundError(f"Source not found: {source_id}")

        self._persist_sources(session_id, filtered)
        self._sync_session_metadata(session_id, filtered)

    def remaining_capacity(self, session_id: str) -> int:
        current = len(self.list_sources(session_id))
        return max(0, self.max_sources_per_session - current)

    def _ensure_capacity(self, current_count: int, additional: int) -> None:
        if current_count + additional > self.max_sources_per_session:
            raise ValueError(
                f"Source limit exceeded. Max per session is {self.max_sources_per_session}."
            )

    def _persist_sources(self, session_id: str, sources: list[SourceMetadata]) -> None:
        self._write_json(
            self._sources_path(session_id),
            [s.model_dump(mode="json") for s in sources],
        )

    def _sync_session_metadata(
        self, session_id: str, sources: list[SourceMetadata]
    ) -> None:
        metadata = self.session_store.get_session_metadata(session_id)
        metadata.source_ids = [s.source_id for s in sources]
        metadata.source_count = len(sources)
        metadata.updated_at = datetime.now(timezone.utc)
        self.session_store.save_session_metadata(metadata)

    @staticmethod
    def _write_json(path: Path, data: object) -> None:
        path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")

    @staticmethod
    def _read_json(path: Path) -> object:
        return json.loads(path.read_text(encoding="utf-8"))