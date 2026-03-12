from app.live_notebook_agent.sub_agents.source_agent import source_agent
from app.live_notebook_agent.sub_agents.web_search_agent import web_search_agent
from app.live_notebook_agent.sub_agents.response_agent import response_agent
from app.live_notebook_agent.sub_agents.recap_manager import recap_manager


def main() -> None:
    print("Agents loaded successfully.")
    print(f"source_agent: {source_agent.name}")
    print(f"web_search_agent: {web_search_agent.name}")
    print(f"response_agent: {response_agent.name}")
    print(f"recap_manager: {recap_manager.name}")


if __name__ == "__main__":
    main()