"""
GCS-based backup and restore for session data.

Backup structure in GCS:
  gs://{bucket}/backups/sessions/{session_id}/session.json
  gs://{bucket}/backups/sessions/{session_id}/messages.json
  gs://{bucket}/backups/sessions/{session_id}/recap.json
  gs://{bucket}/backups/sessions/{session_id}/memory.json
  gs://{bucket}/backups/sessions/{session_id}/sources.json
  gs://{bucket}/backups/sessions/{session_id}/chunks/{source_id}.json

Uploaded source files (PDFs etc.) are already mirrored at:
  gs://{bucket}/sessions/{session_id}/sources/{filename}

This module handles the metadata/index files.
"""

from __future__ import annotations

import asyncio
import json
import logging
from pathlib import Path

from google.cloud import storage

from app.config import get_settings

logger = logging.getLogger(__name__)

_BACKUP_PREFIX = "backups/sessions"
_SESSION_FILES = [
    "session.json",
    "messages.json",
    "recap.json",
    "memory.json",
    "sources.json",
]


class GCSBackupService:
    def __init__(self) -> None:
        settings = get_settings()
        self.bucket_name = settings.gcs_bucket
        self.sessions_dir = Path(settings.sessions_dir).resolve()

    # ── Internal helpers ──────────────────────────────────────────────────────

    def _client(self) -> storage.Client:
        return storage.Client()

    def _gcs_path(self, session_id: str, filename: str) -> str:
        return f"{_BACKUP_PREFIX}/{session_id}/{filename}"

    # ── Sync implementations (run via asyncio.to_thread) ─────────────────────

    def _backup_session_sync(self, session_id: str) -> int:
        """Upload all session metadata files to GCS. Returns file count."""
        session_dir = self.sessions_dir / session_id
        if not session_dir.exists():
            return 0

        client = self._client()
        bucket = client.bucket(self.bucket_name)
        uploaded = 0

        for fname in _SESSION_FILES:
            fpath = session_dir / fname
            if not fpath.exists():
                continue
            blob = bucket.blob(self._gcs_path(session_id, fname))
            blob.upload_from_filename(str(fpath), content_type="application/json")
            uploaded += 1

        # Backup chunks directory
        chunks_dir = session_dir / "chunks"
        if chunks_dir.exists():
            for chunk_file in chunks_dir.glob("*.json"):
                blob = bucket.blob(self._gcs_path(session_id, f"chunks/{chunk_file.name}"))
                blob.upload_from_filename(str(chunk_file), content_type="application/json")
                uploaded += 1

        return uploaded

    def _restore_session_sync(self, session_id: str) -> int:
        """Download session files from GCS to local storage. Returns file count."""
        client = self._client()
        bucket = client.bucket(self.bucket_name)
        prefix = f"{_BACKUP_PREFIX}/{session_id}/"

        blobs = list(bucket.list_blobs(prefix=prefix))
        if not blobs:
            return 0

        session_dir = self.sessions_dir / session_id
        session_dir.mkdir(parents=True, exist_ok=True)
        restored = 0

        for blob in blobs:
            relative = blob.name[len(prefix):]
            if not relative:
                continue
            local_path = session_dir / relative
            local_path.parent.mkdir(parents=True, exist_ok=True)
            blob.download_to_filename(str(local_path))
            restored += 1

        return restored

    def _list_backed_up_sessions_sync(self) -> list[dict]:
        """Return metadata for all sessions backed up in GCS."""
        client = self._client()
        bucket = client.bucket(self.bucket_name)
        prefix = f"{_BACKUP_PREFIX}/"

        seen: set[str] = set()
        results: list[dict] = []

        for blob in bucket.list_blobs(prefix=prefix):
            parts = blob.name[len(prefix):].split("/")
            if not parts or not parts[0]:
                continue
            session_id = parts[0]
            if session_id in seen:
                continue
            seen.add(session_id)

            # Read session.json to get display metadata
            meta_blob = bucket.blob(f"{_BACKUP_PREFIX}/{session_id}/session.json")
            try:
                data = json.loads(meta_blob.download_as_text())
                results.append({
                    "session_id": session_id,
                    "title": data.get("title", "Untitled"),
                    "created_at": data.get("created_at"),
                    "updated_at": data.get("updated_at"),
                    "message_count": data.get("message_count", 0),
                    "local": (self.sessions_dir / session_id / "session.json").exists(),
                })
            except Exception:
                results.append({
                    "session_id": session_id,
                    "title": "Unknown",
                    "created_at": None,
                    "updated_at": None,
                    "message_count": 0,
                    "local": (self.sessions_dir / session_id / "session.json").exists(),
                })

        results.sort(key=lambda x: x.get("updated_at") or "", reverse=True)
        return results

    def _restore_all_sync(self, overwrite: bool = False) -> dict:
        """Restore all GCS sessions to local. Returns summary dict."""
        client = self._client()
        bucket = client.bucket(self.bucket_name)
        prefix = f"{_BACKUP_PREFIX}/"

        session_ids: set[str] = set()
        for blob in bucket.list_blobs(prefix=prefix):
            parts = blob.name[len(prefix):].split("/")
            if parts and parts[0]:
                session_ids.add(parts[0])

        restored = 0
        skipped = 0

        for sid in session_ids:
            local_path = self.sessions_dir / sid / "session.json"
            if local_path.exists() and not overwrite:
                skipped += 1
                continue
            count = self._restore_session_sync(sid)
            if count > 0:
                restored += 1

        return {"restored": restored, "skipped": skipped, "total": len(session_ids)}

    # ── Async public API ──────────────────────────────────────────────────────

    async def backup_session(self, session_id: str) -> int:
        return await asyncio.to_thread(self._backup_session_sync, session_id)

    async def restore_session(self, session_id: str) -> int:
        return await asyncio.to_thread(self._restore_session_sync, session_id)

    async def list_backed_up_sessions(self) -> list[dict]:
        return await asyncio.to_thread(self._list_backed_up_sessions_sync)

    async def restore_all(self, overwrite: bool = False) -> dict:
        return await asyncio.to_thread(self._restore_all_sync, overwrite)


# ── Fire-and-forget helper for use in WebSocket handlers ─────────────────────

def schedule_backup(session_id: str) -> None:
    """Create a background asyncio task to backup a session (non-blocking)."""
    async def _do_backup() -> None:
        try:
            count = await GCSBackupService().backup_session(session_id)
            logger.debug("GCS backup: session %s — %d files", session_id, count)
        except Exception as exc:
            logger.warning("GCS backup failed for %s (non-fatal): %s", session_id, exc)

    try:
        loop = asyncio.get_running_loop()
        loop.create_task(_do_backup())
    except RuntimeError:
        pass  # No running loop — skip backup (e.g. during tests)
