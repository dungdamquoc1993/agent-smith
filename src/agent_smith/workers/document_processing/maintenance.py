"""Independent interval runner for bounded file maintenance."""

import asyncio
import logging
import time

from agent_smith.app.services.file_maintenance import FileMaintenanceService

logger = logging.getLogger(__name__)


class FileMaintenanceRunner:
    def __init__(
        self,
        service: FileMaintenanceService,
        *,
        interval_seconds: float,
        batch_size: int = 100,
    ) -> None:
        self._service = service
        self._interval_seconds = interval_seconds
        self._batch_size = batch_size

    async def run_forever(self, stop_event: asyncio.Event) -> None:
        while not stop_event.is_set():
            try:
                await self.run_once()
            except Exception:
                logger.exception("File maintenance iteration failed")
            try:
                await asyncio.wait_for(stop_event.wait(), timeout=self._interval_seconds)
            except TimeoutError:
                pass

    async def run_once(self) -> dict[str, int]:
        started = time.monotonic()
        summary = {
            "expiredUploads": await self._service.cleanup_stale_uploads(
                limit=self._batch_size
            ),
            "rejectedObjects": await self._service.cleanup_rejected_uploads(
                limit=self._batch_size
            ),
            "purgedFiles": await self._service.cleanup_deleted_files(
                limit=self._batch_size
            ),
            "purgedAuditEvents": await self._service.cleanup_audit_events(
                limit=self._batch_size
            ),
        }
        logger.info(
            "File maintenance completed",
            extra={
                "counts": summary,
                "duration_ms": round((time.monotonic() - started) * 1000),
            },
        )
        return summary
