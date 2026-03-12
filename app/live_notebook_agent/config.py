from __future__ import annotations

from dataclasses import dataclass

from app.config import get_settings


@dataclass(frozen=True)
class AgentSettings:
    model_name: str
    app_name: str


def get_agent_settings() -> AgentSettings:
    settings = get_settings()
    return AgentSettings(
        model_name=settings.live_notebook_agent_model,
        app_name="live_notebook_lm",
    )