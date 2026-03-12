from __future__ import annotations

from google.adk.agents import Agent
from google.adk.tools import google_search

from app.live_notebook_agent.config import get_agent_settings
from app.live_notebook_agent.prompts import WEB_SEARCH_AGENT_INSTRUCTION


agent_settings = get_agent_settings()

web_search_agent = Agent(
    name="web_search_agent",
    model="gemini-2.5-flash",
    description="Agent dedicated to Google Search-based web source discovery.",
    instruction=WEB_SEARCH_AGENT_INSTRUCTION,
    tools=[google_search],
)