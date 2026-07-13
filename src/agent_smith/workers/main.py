"""Worker entrypoint placeholder."""

from __future__ import annotations

from pathlib import Path

from agent_smith.app.container import AppContainer, load_dotenv
from agent_smith.workers.agent_worker import AgentWorker


def create_worker() -> AgentWorker:
    load_dotenv(Path.cwd() / ".env")
    container = AppContainer()
    container.bootstrap_providers()
    return AgentWorker(container)


def main() -> None:
    create_worker()
    print("Agent Smith worker initialized. No queue adapter is wired yet.")


if __name__ == "__main__":
    main()
