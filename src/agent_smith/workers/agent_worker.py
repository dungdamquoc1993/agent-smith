"""Worker boundary for future horizontal scale.

Today HTTP handlers run agent work in-process on the request connection.
This package marks where long-running runs would move when the process
is split into an API tier and dedicated workers.
"""

from __future__ import annotations

from agent_smith.app.container import AppContainer


class AgentWorker:
    def __init__(self, container: AppContainer) -> None:
        self.container = container

    async def run_file_cleanup_once(self, *, limit: int = 100) -> dict[str, int]:
        """Run idempotent file cleanup; a future scheduler may call this repeatedly."""
        return {
            "expiredUploads": await self.container.files.cleanup_stale_uploads(limit=limit),
            "purgedFiles": await self.container.files.cleanup_deleted_files(limit=limit),
        }
