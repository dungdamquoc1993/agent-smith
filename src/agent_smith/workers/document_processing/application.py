"""Worker-process lifecycle for document processing and file maintenance."""

from __future__ import annotations

import asyncio
import signal
from collections.abc import Awaitable, Callable

from agent_smith.workers.document_processing.maintenance import FileMaintenanceRunner
from agent_smith.workers.document_processing.worker import DocumentProcessingWorker


class DocumentWorkerApplication:
    def __init__(
        self,
        worker: DocumentProcessingWorker,
        maintenance: FileMaintenanceRunner,
        *,
        close: Callable[[], Awaitable[None]],
    ) -> None:
        self.worker = worker
        self.maintenance = maintenance
        self._close = close
        self.stop_event = asyncio.Event()
        self._closed = False

    def stop(self) -> None:
        self.stop_event.set()

    def install_signal_handlers(self) -> None:
        loop = asyncio.get_running_loop()
        for name in ("SIGINT", "SIGTERM"):
            signum = getattr(signal, name, None)
            if signum is None:
                continue
            try:
                loop.add_signal_handler(signum, self.stop)
            except NotImplementedError:
                pass

    async def run(self) -> None:
        try:
            async with asyncio.TaskGroup() as tasks:
                tasks.create_task(self.worker.run_forever(self.stop_event))
                tasks.create_task(self.maintenance.run_forever(self.stop_event))
        finally:
            await self.close()

    async def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        await self._close()
