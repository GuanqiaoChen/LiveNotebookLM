from __future__ import annotations

from google.adk.agents import Agent

from app.live_notebook_agent.config import get_agent_settings
from app.live_notebook_agent.prompts import SOURCE_AGENT_INSTRUCTION


agent_settings = get_agent_settings()

source_agent = Agent(
    name="source_agent",
    model=agent_settings.model_name,
    description="Agent for organizing retrieved uploaded-source evidence into a grounded bundle.",
    instruction=SOURCE_AGENT_INSTRUCTION,
    tools=[],
)