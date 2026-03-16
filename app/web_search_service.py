"""
Web search service using the Google ADK web_search_agent via an ADK Runner.

Accepts a plain-text query and returns structured results:
  [ { "title": str, "url": str, "snippet": str }, ... ]
"""
from __future__ import annotations

import json
import re
import uuid
from typing import Any

from google.adk.runners import Runner
from google.adk.sessions import InMemorySessionService
from google.genai import types

from app.live_notebook_agent.sub_agents.web_search_agent import web_search_agent

_APP_NAME = "live-notebook-lm-search"
_USER_ID  = "search-service"

# Module-level singletons — unique session IDs per call make concurrent use safe.
_session_service = InMemorySessionService()
_runner = Runner(
    agent=web_search_agent,
    app_name=_APP_NAME,
    session_service=_session_service,
)


async def search_web(query: str, max_results: int = 10) -> list[dict[str, Any]]:
    """
    Run the ADK web_search_agent and return up to *max_results* structured results.
    Each result is a dict with keys: title, url, snippet.
    Returns an empty list on any error.
    """
    if max_results <= 0:
        return []

    session_id = str(uuid.uuid4())
    await _session_service.create_session(
        app_name=_APP_NAME,
        user_id=_USER_ID,
        session_id=session_id,
    )

    prompt = (
        f'Search for: "{query}"\n\n'
        f'Return the top {max_results} results as a raw JSON array (no markdown, '
        f'no code fences). Each element must have exactly:\n'
        f'  "title"  – page title\n'
        f'  "url"    – full HTTPS URL found via Google Search\n'
        f'  "snippet"– 2-3 sentence summary of the page\n\n'
        f'Respond with ONLY the JSON array.'
    )

    new_message = types.Content(
        role="user",
        parts=[types.Part(text=prompt)],
    )

    final_text = ""
    try:
        async for event in _runner.run_async(
            user_id=_USER_ID,
            session_id=session_id,
            new_message=new_message,
        ):
            if event.is_final_response() and event.content:
                for part in (event.content.parts or []):
                    t = getattr(part, "text", None)
                    if t:
                        final_text += t
    except Exception:
        return []

    return _parse_results(final_text, max_results)


def _parse_results(text: str, max_results: int) -> list[dict[str, Any]]:
    """Extract and normalise a JSON array of web results from the agent response."""
    if not text:
        return []

    # Strip markdown code fences the model may add despite instructions
    text = re.sub(r"```(?:json)?\s*|\s*```", " ", text).strip()

    match = re.search(r"\[[\s\S]*\]", text)
    if not match:
        return []

    try:
        raw = json.loads(match.group())
    except json.JSONDecodeError:
        return []

    results: list[dict[str, Any]] = []
    for item in raw[:max_results]:
        if not isinstance(item, dict):
            continue
        url = str(item.get("url") or "").strip()
        if not url or not url.startswith("http"):
            continue
        results.append({
            "title":   str(item.get("title")   or url).strip(),
            "url":     url,
            "snippet": str(
                item.get("snippet")
                or item.get("description")
                or item.get("summary")
                or ""
            ).strip(),
        })

    return results
