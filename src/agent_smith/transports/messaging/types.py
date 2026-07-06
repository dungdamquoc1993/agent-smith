"""Message bus contracts used by future Redis/RMQ/Kafka adapters."""

from __future__ import annotations

from typing import Protocol

from agent_smith.app.envelope import CommandEnvelope


class CommandConsumer(Protocol):
    async def handle(self, command: CommandEnvelope) -> None: ...


class CommandPublisher(Protocol):
    async def publish(self, command: CommandEnvelope) -> None: ...

