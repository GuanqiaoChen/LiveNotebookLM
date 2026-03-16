from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from app.memory_manager import MemoryManager
from app.schemas import Citation, MessageRecord
from app.session_store import SessionStore
from app.source_store import SourceStore
from app.live_notebook_agent.sub_agents.retriever import Retriever


class LiveOrchestrator:
    """
    Deterministic session orchestrator.

    Responsibilities:
    - persist user / assistant text turns
    - rebuild memory after each turn
    - retrieve evidence from Pinecone
    - build grounded context for the live runtime / response layer
    """

    def __init__(self) -> None:
        self.session_store = SessionStore()
        self.source_store = SourceStore()
        self.memory_manager = MemoryManager()
        self.retriever = Retriever()

    # ── Public API ───────────────────────────────────────────────────────────

    def record_user_message(
        self,
        session_id: str,
        content: str,
        interrupted: bool = False,
    ) -> MessageRecord:
        message = MessageRecord(
            turn_id=self._new_turn_id("user"),
            session_id=session_id,
            role="user",
            content=content,
            timestamp=datetime.now(timezone.utc),
            interrupted=interrupted,
            citations=[],
        )
        self.session_store.append_message(message)
        self.memory_manager.rebuild_memory(session_id)
        return message

    def record_assistant_message(
        self,
        session_id: str,
        content: str,
        citations: list[dict] | None = None,
        interrupted: bool = False,
    ) -> MessageRecord:
        citation_models = [
            Citation(
                source_id=item.get("source_id", ""),
                source_name=item.get("source_name", ""),
                page=item.get("page"),
                section=item.get("section"),
                snippet=(item.get("text") or "")[:280],
                url=item.get("url"),
            )
            for item in (citations or [])
        ]

        message = MessageRecord(
            turn_id=self._new_turn_id("assistant"),
            session_id=session_id,
            role="assistant",
            content=content,
            timestamp=datetime.now(timezone.utc),
            interrupted=interrupted,
            citations=citation_models,
        )
        self.session_store.append_message(message)
        self.memory_manager.rebuild_memory(session_id)
        return message

    def prepare_grounded_turn(
        self,
        session_id: str,
        user_text: str,
        top_k: int = 5,
    ) -> dict[str, Any]:
        metadata = self.session_store.get_session_metadata(session_id)
        sources = self.source_store.list_sources(session_id)
        source_ids = [src.source_id for src in sources]

        memory = self.memory_manager.get_context_for_model(session_id)

        evidence: list[dict] = []
        if source_ids:
            if self.retriever.is_configured():
                evidence = self.retriever.retrieve_with_vertex_query(
                    session_id=session_id,
                    query=user_text,
                    top_k=top_k,
                )
            else:
                evidence = self.retriever.retrieve_local_fallback(
                    session_id=session_id,
                    source_ids=source_ids,
                    query=user_text,
                    top_k=top_k,
                )

        grounded_prompt = self._build_grounded_prompt(
            session_title=metadata.title,
            user_text=user_text,
            memory=memory,
            evidence=evidence,
        )

        return {
            "session_id": session_id,
            "session_title": metadata.title,
            "user_text": user_text,
            "sources": [src.model_dump(mode="json") for src in sources],
            "memory": memory,
            "evidence": evidence,
            "grounded_prompt": grounded_prompt,
        }

    def get_session_messages(self, session_id: str) -> list[dict]:
        messages = self.session_store.get_messages(session_id)
        return [msg.model_dump(mode="json") for msg in messages]

    def get_session_sources(self, session_id: str) -> list[dict]:
        sources = self.source_store.list_sources(session_id)
        return [src.model_dump(mode="json") for src in sources]

    # ── Private helpers ──────────────────────────────────────────────────────

    def _build_grounded_prompt(
        self,
        session_title: str,
        user_text: str,
        memory: dict,
        evidence: list[dict],
    ) -> str:
        lines: list[str] = []

        lines.append(f"Session title: {session_title}")
        lines.append("")
        lines.append("Conversation memory summary:")
        lines.append(memory.get("rolling_summary") or "(none)")
        lines.append("")

        recent_messages = memory.get("recent_messages", [])
        if recent_messages:
            lines.append("Recent messages:")
            for msg in recent_messages:
                role = msg.get("role", "unknown")
                content = (msg.get("content") or "").strip()
                if content:
                    lines.append(f"- {role}: {content}")
            lines.append("")

        open_questions = memory.get("open_questions", [])
        if open_questions:
            lines.append("Open questions:")
            for item in open_questions:
                lines.append(f"- {item}")
            lines.append("")

        lines.append(f"Current user request: {user_text}")
        lines.append("")

        if evidence:
            lines.append("Retrieved source evidence:")
            for idx, item in enumerate(evidence, start=1):
                source_name = item.get("source_name", "unknown")
                page = item.get("page")
                section = item.get("section")
                snippet = (item.get("text") or "").strip()

                location_parts = []
                if page is not None:
                    location_parts.append(f"page {page}")
                if section:
                    location_parts.append(f"section {section}")
                location = ", ".join(location_parts) if location_parts else "location unknown"

                lines.append(f"[{idx}] {source_name} ({location})")
                lines.append(f"Snippet: {snippet[:600]}")
            lines.append("")
            lines.append(
                "Answer the user naturally in a voice-first style, grounding the response in the evidence above. "
                "Do not fabricate claims outside the evidence. If the evidence is insufficient, say so clearly."
            )
        else:
            lines.append(
                "No uploaded-source evidence was found for this turn. "
                "Answer carefully and explicitly say when uploaded sources do not provide enough support."
            )

        return "\n".join(lines)

    @staticmethod
    def _new_turn_id(prefix: str) -> str:
        now = datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S%f")
        return f"{prefix}_{now}"