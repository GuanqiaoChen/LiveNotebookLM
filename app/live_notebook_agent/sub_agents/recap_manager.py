from __future__ import annotations

from google.adk.agents import Agent

from app.live_notebook_agent.config import get_agent_settings
from app.live_notebook_agent.prompts import RECAP_AGENT_INSTRUCTION


agent_settings = get_agent_settings()

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

    return "\n".join(lines)