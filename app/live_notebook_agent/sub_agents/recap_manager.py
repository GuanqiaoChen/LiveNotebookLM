from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path

from google import genai
from google.adk.agents import Agent

from app.config import get_settings
from app.schemas import FollowUpResponse, RecapData
from app.live_notebook_agent.config import get_agent_settings
from app.live_notebook_agent.prompts import FOLLOW_UP_AGENT_INSTRUCTION, RECAP_AGENT_INSTRUCTION


agent_settings = get_agent_settings()
settings = get_settings()

recap_manager = Agent(
    name="recap_manager",
    model=agent_settings.model_name,
    description="Agent for generating structured recap from saved text conversation.",
    instruction=RECAP_AGENT_INSTRUCTION,
    tools=[],
)


def build_recap_input(messages: list[dict], sources: list[dict]) -> str:
    lines: list[str] = []
    lines.append("Conversation messages:")
    for msg in messages:
        role = msg.get("role", "unknown")
        content = (msg.get("content") or "").strip()
        if content:
            lines.append(f"{role}: {content}")

    lines.append("")
    lines.append("Sources:")
    for src in sources:
        display_name = src.get("display_name", "unknown source")
        source_url = src.get("source_url")
        gcs_uri = src.get("gcs_uri")
        if source_url:
            lines.append(f"- {display_name} ({source_url})")
        elif gcs_uri:
            lines.append(f"- {display_name} ({gcs_uri})")
        else:
            lines.append(f"- {display_name}")

    lines.append("")
    lines.append(
        "Generate a recap, not a transcript copy. "
        "Return JSON with keys: topic, key_insights, sources_referenced, open_questions, next_steps."
    )
    return "\n".join(lines)


def generate_recap_data(
    session_id: str,
    messages: list[dict],
    sources: list[dict],
) -> RecapData:
    client = genai.Client(
        vertexai=True,
        project=settings.google_cloud_project,
        location=settings.google_cloud_location,
    )

    prompt = build_recap_input(messages, sources)

    response = client.models.generate_content(
        model="gemini-2.5-flash",
        contents=prompt,
        config={
            "response_mime_type": "application/json",
        },
    )

    raw_text = (response.text or "").strip()
    parsed = json.loads(raw_text) if raw_text else {}

    recap = RecapData(
        session_id=session_id,
        topic=parsed.get("topic", ""),
        key_insights=parsed.get("key_insights", []) or [],
        sources_referenced=parsed.get("sources_referenced", []) or [],
        open_questions=parsed.get("open_questions", []) or [],
        next_steps=parsed.get("next_steps", []) or [],
        generated_at=datetime.now(timezone.utc),
    )
    return recap


def save_recap_data(session_id: str, recap: RecapData) -> None:
    recap_path = _recap_path(session_id)
    recap_path.write_text(
        json.dumps(recap.model_dump(mode="json"), indent=2, ensure_ascii=False),
        encoding="utf-8",
    )


def load_recap_data(session_id: str) -> RecapData | None:
    recap_path = _recap_path(session_id)
    if not recap_path.exists():
        return None

    data = json.loads(recap_path.read_text(encoding="utf-8"))
    if not data:
        return None

    return RecapData.model_validate(data)


def generate_follow_up_suggestions(
    session_id: str,
    messages: list[dict],
) -> FollowUpResponse:
    """Generate exactly 3 follow-up question/topic suggestions from the conversation."""
    client = genai.Client(
        vertexai=True,
        project=settings.google_cloud_project,
        location=settings.google_cloud_location,
    )

    lines: list[str] = ["Conversation (most recent turns):"]
    for msg in messages[-20:]:
        role = msg.get("role", "unknown")
        content = (msg.get("content") or "").strip()
        if content and role in ("user", "assistant"):
            lines.append(f"{role}: {content[:400]}")

    lines.append("")
    lines.append(
        "Based on this conversation, generate exactly 3 specific follow-up questions "
        "or topics the user could explore next. "
        "Return ONLY a raw JSON array of exactly 3 strings. No markdown, no code fences."
    )

    response = client.models.generate_content(
        model="gemini-2.5-flash",
        contents="\n".join(lines),
        config={"response_mime_type": "application/json"},
    )

    raw_text = re.sub(r"```(?:json)?\s*|\s*```", " ", (response.text or "")).strip()

    try:
        parsed = json.loads(raw_text)
        suggestions = [str(s) for s in (parsed if isinstance(parsed, list) else [])][:3]
    except (json.JSONDecodeError, ValueError):
        suggestions = []

    while len(suggestions) < 3:
        suggestions.append("Explore this topic further")

    return FollowUpResponse(
        session_id=session_id,
        suggestions=suggestions,
        generated_at=datetime.now(timezone.utc),
    )


def _recap_path(session_id: str) -> Path:
    base_dir = Path(settings.sessions_dir).resolve()
    session_dir = base_dir / session_id
    session_dir.mkdir(parents=True, exist_ok=True)
    return session_dir / "recap.json"