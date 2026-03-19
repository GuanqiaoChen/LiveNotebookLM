from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from pathlib import Path

from app.config import get_settings
from app.schemas import MessageRecord, SessionDetail, SessionMetadata


class SessionStore:
    """
    Filesystem-backed store for session metadata and conversation messages.

    Each session lives under sessions/{client_id}/{session_id}/ and contains:
    - session.json  — SessionMetadata
    - messages.json — list of MessageRecord
    - recap.json    — optional recap payload

    client_id namespaces all data per browser so every visitor has an isolated
    workspace. Defaults to "default" for backwards-compat / tests.
    """

    def __init__(self, client_id: str = "default") -> None:
        settings = get_settings()
        self.base_dir = (Path(settings.sessions_dir) / client_id).resolve()
        self.base_dir.mkdir(parents=True, exist_ok=True)

    # ── Private path helpers ─────────────────────────────────────────────────

    def _session_dir(self, session_id: str) -> Path:
        return self.base_dir / session_id

    def _metadata_path(self, session_id: str) -> Path:
        return self._session_dir(session_id) / "session.json"

    def _messages_path(self, session_id: str) -> Path:
        return self._session_dir(session_id) / "messages.json"

    def _recap_path(self, session_id: str) -> Path:
        return self._session_dir(session_id) / "recap.json"

    # ── Session CRUD ─────────────────────────────────────────────────────────

    def create_session(self, title: str | None = None, voice: str | None = None) -> SessionMetadata:
        session_id = str(uuid.uuid4())
        now = datetime.now(timezone.utc)
        final_title = title.strip() if title and title.strip() else "Untitled Session"

        metadata = SessionMetadata(
            session_id=session_id,
            title=final_title,
            created_at=now,
            updated_at=now,
            source_ids=[],
            source_count=0,
            message_count=0,
            is_active=True,
            ended_at=None,
            voice=voice or "Aoede",
        )

        session_dir = self._session_dir(session_id)
        session_dir.mkdir(parents=True, exist_ok=True)

        self._write_json(self._metadata_path(session_id), metadata.model_dump(mode="json"))
        self._write_json(self._messages_path(session_id), [])
        if not self._recap_path(session_id).exists():
            self._write_json(self._recap_path(session_id), None)

        return metadata

    def list_sessions(self) -> list[SessionMetadata]:
        sessions: list[SessionMetadata] = []
        for child in self.base_dir.iterdir():
            if not child.is_dir():
                continue
            metadata_path = child / "session.json"
            if metadata_path.exists():
                data = self._read_json(metadata_path)
                sessions.append(SessionMetadata.model_validate(data))

        sessions.sort(key=lambda s: s.updated_at, reverse=True)
        return sessions

    def get_session_metadata(self, session_id: str) -> SessionMetadata:
        path = self._metadata_path(session_id)
        if not path.exists():
            raise FileNotFoundError(f"Session not found: {session_id}")
        return SessionMetadata.model_validate(self._read_json(path))

    def save_session_metadata(self, metadata: SessionMetadata) -> None:
        self._write_json(
            self._metadata_path(metadata.session_id),
            metadata.model_dump(mode="json"),
        )

    # ── Messages ─────────────────────────────────────────────────────────────

    def get_messages(self, session_id: str) -> list[MessageRecord]:
        path = self._messages_path(session_id)
        if not path.exists():
            raise FileNotFoundError(f"Messages file not found for session: {session_id}")

        data = self._read_json(path)
        return [MessageRecord.model_validate(item) for item in data]

    def append_message(self, message: MessageRecord) -> None:
        messages = self.get_messages(message.session_id)
        messages.append(message)

        self._write_json(
            self._messages_path(message.session_id),
            [m.model_dump(mode="json") for m in messages],
        )

        metadata = self.get_session_metadata(message.session_id)
        metadata.message_count = len(messages)
        metadata.updated_at = datetime.now(timezone.utc)
        self.save_session_metadata(metadata)

    def update_session_title(self, session_id: str, title: str) -> SessionMetadata:
        metadata = self.get_session_metadata(session_id)
        metadata.title = title.strip() or "Untitled Session"
        metadata.updated_at = datetime.now(timezone.utc)
        self.save_session_metadata(metadata)
        return metadata

    def mark_session_ended(self, session_id: str) -> SessionMetadata:
        metadata = self.get_session_metadata(session_id)
        metadata.is_active = False
        metadata.ended_at = datetime.now(timezone.utc)
        metadata.updated_at = metadata.ended_at
        self.save_session_metadata(metadata)
        return metadata

    def get_session_detail(self, session_id: str) -> SessionDetail:
        metadata = self.get_session_metadata(session_id)
        messages = self.get_messages(session_id)
        return SessionDetail(metadata=metadata, messages=messages, sources=[])

    def delete_session(self, session_id: str) -> None:
        session_dir = self._session_dir(session_id)
        if not session_dir.exists():
            raise FileNotFoundError(f"Session not found: {session_id}")

        for path in sorted(session_dir.rglob("*"), reverse=True):
            if path.is_file():
                path.unlink()
            elif path.is_dir():
                path.rmdir()
        session_dir.rmdir()

    def export_backup_payload(self, session_id: str) -> dict:
        """Return session metadata, messages, and recap as a serialisable dict."""
        metadata = self.get_session_metadata(session_id)
        messages = self.get_messages(session_id)

        recap = None
        recap_path = self._recap_path(session_id)
        if recap_path.exists():
            recap = self._read_json(recap_path)

        return {
            "session": metadata.model_dump(mode="json"),
            "messages": [m.model_dump(mode="json") for m in messages],
            "recap": recap,
        }

    # ── I/O utilities ────────────────────────────────────────────────────────

    @staticmethod
    def _write_json(path: Path, data: object) -> None:
        path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")

    @staticmethod
    def _read_json(path: Path) -> object:
        return json.loads(path.read_text(encoding="utf-8"))