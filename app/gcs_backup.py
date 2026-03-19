"""
GCS-based backup and restore for session data.

Backup structure in GCS:
  gs://{bucket}/backups/{client_id}/sessions/{session_id}/session.json
  gs://{bucket}/backups/{client_id}/sessions/{session_id}/messages.json
  gs://{bucket}/backups/{client_id}/sessions/{session_id}/recap.json
  gs://{bucket}/backups/{client_id}/sessions/{session_id}/memory.json
  gs://{bucket}/backups/{client_id}/sessions/{session_id}/sources.json
  gs://{bucket}/backups/{client_id}/sessions/{session_id}/chunks/{source_id}.json

Uploaded source files (PDFs etc.) are already mirrored at:
  gs://{bucket}/sessions/{session_id}/sources/{filename}

client_id is the per-browser identity from the X-Client-ID header.
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

_BACKUP_ROOT = "backups"
_SESSION_FILES = [
    "session.json",
    "messages.json",
    "recap.json",
    "memory.json",
    "sources.json",
]


class GCSBackupService:
    """Upload and restore session metadata files to/from Google Cloud Storage."""

    def __init__(self, client_id: str = "default") -> None:
        settings = get_settings()
        self.bucket_name = settings.gcs_bucket
        self.client_id = client_id
        # Local sessions are scoped under sessions/{client_id}/
        self.sessions_dir = (Path(settings.sessions_dir) / client_id).resolve()

    # ── Internal helpers ──────────────────────────────────────────────────────

    def _client(self) -> storage.Client:
        return storage.Client()

    def _gcs_path(self, session_id: str, filename: str) -> str:
        return f"{_BACKUP_ROOT}/{self.client_id}/sessions/{session_id}/{filename}"

    def _gcs_prefix(self, session_id: str) -> str:
        return f"{_BACKUP_ROOT}/{self.client_id}/sessions/{session_id}/"

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
        prefix = self._gcs_prefix(session_id)

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
        """Return metadata for all sessions backed up in GCS for this client."""
        client = self._client()
        bucket = client.bucket(self.bucket_name)
        prefix = f"{_BACKUP_ROOT}/{self.client_id}/sessions/"

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
            meta_blob = bucket.blob(self._gcs_path(session_id, "session.json"))
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
        """Restore all GCS sessions for this client to local. Returns summary dict."""
        client = self._client()
        bucket = client.bucket(self.bucket_name)
        prefix = f"{_BACKUP_ROOT}/{self.client_id}/sessions/"

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

    def _restore_all_users_sync(self, overwrite: bool = False) -> dict:
        """
        Restore sessions for ALL clients from GCS (used on cold-start).

        Scans backups/{client_id}/sessions/{session_id}/ and restores each
        to sessions/{client_id}/{session_id}/ locally.
        """
        from app.config import get_settings as _get_settings
        base_dir = (Path(_get_settings().sessions_dir)).resolve()

        client = self._client()
        bucket = client.bucket(self.bucket_name)
        prefix = f"{_BACKUP_ROOT}/"

        # Collect (client_id, session_id) pairs
        pairs: set[tuple[str, str]] = set()
        for blob in bucket.list_blobs(prefix=prefix):
            # blob.name format: backups/{client_id}/sessions/{session_id}/file
            relative = blob.name[len(prefix):]
            parts = relative.split("/")
            # parts[0]=client_id, parts[1]="sessions", parts[2]=session_id
            if len(parts) >= 3 and parts[0] and parts[1] == "sessions" and parts[2]:
                pairs.add((parts[0], parts[2]))

        restored = 0
        skipped = 0

        for cid, sid in pairs:
            local_path = base_dir / cid / sid / "session.json"
            if local_path.exists() and not overwrite:
                skipped += 1
                continue
            svc = GCSBackupService(client_id=cid)
            count = svc._restore_session_sync(sid)
            if count > 0:
                restored += 1

        return {"restored": restored, "skipped": skipped, "total": len(pairs)}

    # ── Async public API ──────────────────────────────────────────────────────

    async def backup_session(self, session_id: str) -> int:
        return await asyncio.to_thread(self._backup_session_sync, session_id)

    async def restore_session(self, session_id: str) -> int:
        return await asyncio.to_thread(self._restore_session_sync, session_id)

    async def list_backed_up_sessions(self) -> list[dict]:
        return await asyncio.to_thread(self._list_backed_up_sessions_sync)

    async def restore_all(self, overwrite: bool = False) -> dict:
        return await asyncio.to_thread(self._restore_all_sync, overwrite)

    async def restore_all_users(self, overwrite: bool = False) -> dict:
        """Restore sessions for all clients — used on container cold-start."""
        return await asyncio.to_thread(self._restore_all_users_sync, overwrite)


# ── Fire-and-forget helper for use in WebSocket handlers ─────────────────────

def schedule_backup(session_id: str, client_id: str = "default") -> None:
    """Create a background asyncio task to backup a session (non-blocking)."""
    async def _do_backup() -> None:
        try:
            count = await GCSBackupService(client_id=client_id).backup_session(session_id)
            logger.debug("GCS backup: %s/%s — %d files", client_id, session_id, count)
        except Exception as exc:
            logger.warning("GCS backup failed for %s/%s (non-fatal): %s", client_id, session_id, exc)

    try:
        loop = asyncio.get_running_loop()
        loop.create_task(_do_backup())
    except RuntimeError:
        pass  # No running loop — skip backup (e.g. during tests)
