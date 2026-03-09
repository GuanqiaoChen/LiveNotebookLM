"""
LiveNotebookLM Agent definition.

Uses Google ADK with Gemini Live API Toolkit for real-time voice interaction.
Model must support Live API (e.g., gemini-2.5-flash-native-audio-preview or Vertex AI equivalent).
"""

import os

from google.adk.agents import Agent

# TODO: Add tools (e.g., document grounding, google_search) for NotebookLM-like capabilities
# from google.adk.tools import google_search

root_agent = Agent(
    name="live_notebook_agent",
    model=os.getenv("LIVE_NOTEBOOK_AGENT_MODEL"),
    description="LiveNotebookLM agent with real-time voice conversation over documents.",
    instruction="You are LiveNotebookLM, a helpful AI assistant with voice interaction. "
    "You help users understand and discuss their documents through natural conversation.",
    tools=[],  # TODO: Add document grounding tools
)
