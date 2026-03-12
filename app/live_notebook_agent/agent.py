from app.live_notebook_agent.sub_agents import (
    response_agent,
    source_agent,
    web_search_agent,
    recap_manager,
)

AGENT_REGISTRY = {
    "response_agent": response_agent,
    "source_agent": source_agent,
    "web_search_agent": web_search_agent,
    "recap_manager": recap_manager,
}

__all__ = [
    "response_agent",
    "source_agent",
    "web_search_agent",
    "recap_manager",
    "AGENT_REGISTRY",
]