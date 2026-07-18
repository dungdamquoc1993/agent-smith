"""Process-readiness contracts used by the application runtime service."""

from typing import Protocol


class ReadinessCheck(Protocol):
    async def check(self) -> None: ...
