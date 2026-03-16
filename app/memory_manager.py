from __future__ import annotations

import json
from pathlib import Path

from app.config import get_settings
from app.schemas import MessageRecord
from app.session_store import SessionStore


class MemoryManager:
    """
    Maintains per-session rolling memory from conversation history.

    Stores recent messages, a rolling summary of older turns, open questions,
    and key topics — all written to sessions/{id}/memory.json.
    """

    def __init__(self) -> None:
        settings = get_settings()
        self.base_dir = Path(settings.sessions_dir).resolve()
        self.session_store = SessionStore()

    # ── Public API ───────────────────────────────────────────────────────────

    def get_memory(self, session_id: str) -> dict:
        path = self._memory_path(session_id)
        if not path.exists():
            return {
                "session_id": session_id,
                "recent_messages": [],
                "rolling_summary": "",
                "open_questions": [],
                "key_topics": [],
            }

        return json.loads(path.read_text(encoding="utf-8"))

    def rebuild_memory(self, session_id: str, recent_limit: int = 8) -> dict:
        messages = self.session_store.get_messages(session_id)
        recent = messages[-recent_limit:]

        rolling_summary = self._build_summary(
            messages[:-recent_limit] if len(messages) > recent_limit else []
        )
        open_questions = self._extract_open_questions(messages)
        key_topics = self._extract_key_topics(messages)

        payload = {
            "session_id": session_id,
            "recent_messages": [m.model_dump(mode="json") for m in recent],
            "rolling_summary": rolling_summary,
            "open_questions": open_questions,
            "key_topics": key_topics,
        }

        self._write_memory(session_id, payload)
        return payload

    def get_context_for_model(self, session_id: str) -> dict:
        memory = self.get_memory(session_id)
        if not memory["recent_messages"] and not memory["rolling_summary"]:
            memory = self.rebuild_memory(session_id)

        return {
            "rolling_summary": memory.get("rolling_summary", ""),
            "recent_messages": memory.get("recent_messages", []),
            "open_questions": memory.get("open_questions", []),
            "key_topics": memory.get("key_topics", []),
        }

    # ── Private helpers ──────────────────────────────────────────────────────

    def _build_summary(self, messages: list[MessageRecord]) -> str:
        if not messages:
            return ""

        lines: list[str] = []
        for m in messages[-20:]:
            role = m.role.capitalize()
            content = m.content.strip().replace("\n", " ")
            if not content:
                continue
            trimmed = content[:240]
            lines.append(f"{role}: {trimmed}")

        return "\n".join(lines)

    def _extract_open_questions(self, messages: list[MessageRecord]) -> list[str]:
        questions: list[str] = []
        for m in messages:
            if m.role == "user" and "?" in m.content:
                questions.append(m.content.strip())
        return questions[-5:]

    def _extract_key_topics(self, messages: list[MessageRecord]) -> list[str]:
        topics: list[str] = []
        for m in messages:
            if m.role == "user":
                text = m.content.strip()
                if len(text) > 8:
                    topics.append(text[:80])
        return topics[-5:]

    def _memory_path(self, session_id: str) -> Path:
        session_dir = self.base_dir / session_id
        session_dir.mkdir(parents=True, exist_ok=True)
        return session_dir / "memory.json"

    def _write_memory(self, session_id: str, payload: dict) -> None:
        path = self._memory_path(session_id)
        path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")