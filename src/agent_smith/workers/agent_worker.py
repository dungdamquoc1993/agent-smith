"""Dedicated durable document-processing worker."""

from __future__ import annotations

import asyncio
import logging
import random
import time
import uuid
from datetime import UTC, datetime, timedelta

from agent_smith.app.container import AppContainer
from agent_smith.app.ports.document_processing import (
    FileProcessingError,
    PendingDerivative,
    ProcessingInput,
)
from agent_smith.app.ports.files import BlobStorageError
from agent_smith.infra.document_processing import (
    SupportedFileTypeDetector,
    create_processor_registry,
)
from agent_smith.infra.document_processing.serialization import (
    artifact_identity,
    build_core_artifacts,
)

logger = logging.getLogger(__name__)


class AgentWorker:
    def __init__(self, container: AppContainer) -> None:
        self.container = container
        self.worker_id = f"worker-{uuid.uuid4().hex[:12]}"
        self._detector = SupportedFileTypeDetector()
        self._processors = create_processor_registry()
        self._stopping = asyncio.Event()

    async def run_forever(self) -> None:
        settings = self.container.settings
        last_reconciliation = datetime.min.replace(tzinfo=UTC)
        last_maintenance = datetime.min.replace(tzinfo=UTC)
        while not self._stopping.is_set():
            try:
                if (
                    datetime.now(UTC) - last_maintenance
                ).total_seconds() >= settings.file_maintenance_interval_seconds:
                    try:
                        await self.run_file_cleanup_once(limit=100)
                    except Exception:
                        logger.exception("File maintenance iteration failed")
                    finally:
                        # Maintenance failure must not starve document processing.
                        last_maintenance = datetime.now(UTC)
                if (datetime.now(UTC) - last_reconciliation).total_seconds() >= 60:
                    await self.container.file_processing_store.reconcile_uploaded(
                        pipeline_version=settings.file_processing_pipeline_version,
                        max_attempts=settings.file_processing_max_attempts,
                    )
                    last_reconciliation = datetime.now(UTC)
                handled = await self.run_processing_once()
            except Exception:
                logger.exception("Document worker iteration failed")
                handled = False
            if handled:
                continue
            try:
                await asyncio.wait_for(
                    self._stopping.wait(), timeout=settings.file_processing_poll_seconds
                )
            except TimeoutError:
                pass

    def stop(self) -> None:
        self._stopping.set()

    async def run_processing_once(self) -> bool:
        settings = self.container.settings
        claimed = await self.container.file_processing_store.claim_next(
            worker_id=self.worker_id,
            lease_seconds=settings.file_processing_lease_seconds,
        )
        if claimed is None:
            return False
        job, file = claimed
        heartbeat = asyncio.create_task(self._heartbeat(job.id))
        try:
            await asyncio.wait_for(
                self._process(job.id, job.pipeline_version, file),
                timeout=settings.file_processing_timeout_seconds,
            )
        except TimeoutError:
            await self._handle_error(
                job.id,
                job.attempts,
                job.max_attempts,
                FileProcessingError(
                    "processing_timeout", "Document processing timed out.", retryable=True
                ),
            )
        except FileProcessingError as exc:
            await self._handle_error(job.id, job.attempts, job.max_attempts, exc)
        except BlobStorageError:
            await self._handle_error(
                job.id,
                job.attempts,
                job.max_attempts,
                FileProcessingError(
                    "storage_unavailable", "Object storage is temporarily unavailable.", retryable=True
                ),
            )
        except Exception:
            logger.exception("Unexpected document processor failure", extra={"job_id": job.id})
            await self._handle_error(
                job.id,
                job.attempts,
                job.max_attempts,
                FileProcessingError(
                    "processor_failure", "Document processor failed unexpectedly.", retryable=True
                ),
            )
        finally:
            heartbeat.cancel()
            try:
                await heartbeat
            except asyncio.CancelledError:
                pass
        return True

    async def _process(self, job_id: str, pipeline_version: str, file) -> None:
        settings = self.container.settings
        data = await self.container.blob_store.read_object(
            object_key=file.object_key,
            max_bytes=settings.file_max_bytes,
        )
        await self._progress(job_id, "detecting", 15)
        detected = self._detector.detect(
            data=data,
            declared_mime_type=file.mime_type,
            filename=file.original_name,
        )
        if not _mime_compatible(file.mime_type, detected.mime_type):
            raise FileProcessingError(
                "mime_mismatch",
                "Detected content does not match the declared MIME type.",
                retryable=False,
            )
        processor = self._processors.resolve(detected.mime_type)
        owned = await self.container.file_processing_store.set_detected_type(
            job_id=job_id,
            worker_id=self.worker_id,
            detected_mime_type=detected.mime_type,
            processor=f"{processor.name}:{processor.version}",
        )
        if not owned:
            raise FileProcessingError("lease_lost", "Processing lease was lost.", retryable=True)
        await self._progress(job_id, "extracting", 30)

        async def report(_phase: str, percent: int) -> None:
            await self._progress(job_id, "extracting", max(30, min(70, percent)))

        result = await processor.process(
            ProcessingInput(
                file=file,
                detected_mime_type=detected.mime_type,
                data=data,
                report_progress=report,
            )
        )
        await self._progress(job_id, "normalizing", 75)
        artifacts = [
            *build_core_artifacts(result.document),
            *result.page_images,
            *result.artifacts,
        ]
        pending: list[PendingDerivative] = []
        await self._progress(job_id, "uploading", 85)
        for artifact in artifacts:
            derivative_id, relative_key = artifact_identity(
                file_id=file.id,
                pipeline_version=pipeline_version,
                artifact=artifact,
            )
            object_key = f"principals/{file.principal_id}/{relative_key}"
            stat = await self.container.blob_store.write_object(
                object_key=object_key,
                data=artifact.data,
                mime_type=artifact.mime_type,
            )
            pending.append(
                PendingDerivative(
                    id=derivative_id,
                    kind=artifact.kind,
                    object_key=object_key,
                    mime_type=artifact.mime_type,
                    size_bytes=stat.size_bytes,
                    metadata=artifact.metadata,
                )
            )
        await self._progress(job_id, "finalizing", 95)
        metadata = {
            **result.metadata,
            "schemaVersion": result.document.schema_version,
            "pipelineVersion": pipeline_version,
            "warnings": list(result.warnings),
        }
        completed = await self.container.file_processing_store.complete_job(
            job_id=job_id,
            worker_id=self.worker_id,
            derivatives=pending,
            processing_metadata=metadata,
        )
        if not completed:
            raise FileProcessingError("lease_lost", "Processing lease was lost.", retryable=True)

    async def _heartbeat(self, job_id: str) -> None:
        settings = self.container.settings
        while True:
            await asyncio.sleep(settings.file_processing_heartbeat_seconds)
            try:
                alive = await self.container.file_processing_store.heartbeat(
                    job_id=job_id,
                    worker_id=self.worker_id,
                    lease_seconds=settings.file_processing_lease_seconds,
                )
            except Exception:
                logger.exception("Document worker heartbeat failed", extra={"job_id": job_id})
                continue
            if not alive:
                return

    async def _progress(self, job_id: str, phase: str, percent: int) -> None:
        await self.container.file_processing_store.update_progress(
            job_id=job_id,
            worker_id=self.worker_id,
            phase=phase,
            progress_percent=percent,
        )

    async def _handle_error(
        self,
        job_id: str,
        attempts: int,
        max_attempts: int,
        error: FileProcessingError,
    ) -> None:
        payload = {
            "code": error.code,
            "message": error.message,
            "retryable": error.retryable,
        }
        if not error.retryable:
            logger.info(
                "Document processing failed permanently",
                extra={"job_id": job_id, "error_code": error.code},
            )
            await self.container.file_processing_store.fail_job(
                job_id=job_id, worker_id=self.worker_id, error=payload
            )
            return
        cap = min(300.0, 5.0 * (2 ** max(0, attempts - 1)))
        available_at = datetime.now(UTC) + timedelta(seconds=random.uniform(0, cap))
        logger.info(
            "Document processing scheduled for retry",
            extra={"job_id": job_id, "error_code": error.code, "attempts": attempts},
        )
        await self.container.file_processing_store.schedule_retry(
            job_id=job_id,
            worker_id=self.worker_id,
            error=payload,
            available_at=available_at,
        )

    async def run_file_cleanup_once(self, *, limit: int = 100) -> dict[str, int]:
        """Run the bounded, idempotent file-maintenance groups."""
        started = time.monotonic()
        summary = {
            "expiredUploads": await self.container.files.cleanup_stale_uploads(limit=limit),
            "rejectedObjects": await self.container.files.cleanup_rejected_uploads(limit=limit),
            "purgedFiles": await self.container.files.cleanup_deleted_files(limit=limit),
            "purgedAuditEvents": await self.container.files.cleanup_audit_events(limit=limit),
        }
        duration_ms = round((time.monotonic() - started) * 1000)
        logger.info(
            "File maintenance completed",
            extra={"counts": summary, "duration_ms": duration_ms},
        )
        return summary


def _mime_compatible(declared: str, detected: str) -> bool:
    text_types = {"text/plain", "text/markdown", "text/csv"}
    return declared == detected or (declared in text_types and detected in text_types)
