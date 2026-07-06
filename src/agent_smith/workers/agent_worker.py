"""Worker skeleton for future queue-backed agent commands."""

from __future__ import annotations

from agent_smith.app.container import AppContainer
from agent_smith.app.envelope import CommandEnvelope


class AgentWorker:
    def __init__(self, container: AppContainer) -> None:
        self.container = container

    async def handle(self, command: CommandEnvelope) -> None:
        if command.payload.get("type") == "seed_resources":
            await self.container.resources.seed_default_agent()
            return
        raise ValueError(f"Unsupported worker command: {command.payload.get('type')}")

