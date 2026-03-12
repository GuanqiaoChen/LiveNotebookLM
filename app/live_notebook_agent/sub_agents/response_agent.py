from __future__ import annotations

from google.adk.agents import Agent

from app.live_notebook_agent.config import get_agent_settings
from app.live_notebook_agent.prompts import RESPONSE_AGENT_INSTRUCTION


agent_settings = get_agent_settings()

response_agent = Agent(
    name="response_agent",
    model=agent_settings.model_name,
    description="Final response agent for grounded, voice-first answers.",
    instruction=RESPONSE_AGENT_INSTRUCTION,
    tools=[],
)