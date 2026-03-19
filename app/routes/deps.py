"""FastAPI shared dependencies."""

from __future__ import annotations

import re

from fastapi import Header


def get_client_id(x_client_id: str = Header(default="")) -> str:
    """
    Extract a per-browser client ID from the X-Client-ID request header.

    The frontend generates a UUID on first visit and persists it in localStorage,
    then sends it on every request. This scopes all session data to that browser,
    giving each visitor their own isolated workspace without requiring login.

    Only alphanumeric characters and hyphens are accepted; anything else is stripped.
    Falls back to "default" when the header is absent (e.g. curl / tests).
    """
    clean = re.sub(r"[^a-zA-Z0-9\-]", "", x_client_id or "")
    return clean[:64] or "default"
