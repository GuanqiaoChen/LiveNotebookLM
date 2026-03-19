"""
Backup and restore endpoints.

Endpoints:
  GET  /backup/sessions              — list all GCS-backed-up sessions for this client
  POST /backup/sessions/{id}         — backup specific session to GCS
  POST /backup/sessions/{id}/restore — restore specific session from GCS
  POST /backup/restore-all           — restore all GCS sessions not present locally
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException

from app.gcs_backup import GCSBackupService
from app.routes.deps import get_client_id

router = APIRouter(prefix="/backup", tags=["backup"])


@router.get("/sessions")
async def list_backed_up_sessions(client_id: str = Depends(get_client_id)) -> list[dict]:
    """List all sessions available in GCS backup for this client."""
    try:
        return await GCSBackupService(client_id=client_id).list_backed_up_sessions()
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"GCS error: {exc}") from exc


@router.post("/sessions/{session_id}")
async def backup_session(
    session_id: str,
    client_id: str = Depends(get_client_id),
) -> dict:
    """Back up a specific session's data files to GCS."""
    try:
        count = await GCSBackupService(client_id=client_id).backup_session(session_id)
        return {"status": "ok", "session_id": session_id, "files_backed_up": count}
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Backup failed: {exc}") from exc


@router.post("/sessions/{session_id}/restore")
async def restore_session(
    session_id: str,
    client_id: str = Depends(get_client_id),
) -> dict:
    """Restore a specific session from GCS to local storage."""
    try:
        count = await GCSBackupService(client_id=client_id).restore_session(session_id)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Restore failed: {exc}") from exc

    if count == 0:
        raise HTTPException(
            status_code=404,
            detail=f"No backup found in GCS for session: {session_id}",
        )
    return {"status": "ok", "session_id": session_id, "files_restored": count}


@router.post("/restore-all")
async def restore_all_sessions(
    overwrite: bool = False,
    client_id: str = Depends(get_client_id),
) -> dict:
    """
    Restore all sessions from GCS that are not present locally for this client.
    Set overwrite=true to re-download even if local copy exists.
    """
    try:
        result = await GCSBackupService(client_id=client_id).restore_all(overwrite=overwrite)
        return {"status": "ok", **result}
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Restore failed: {exc}") from exc
