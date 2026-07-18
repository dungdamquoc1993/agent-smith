"""Durable document-processing loop with explicit dependencies."""

from __future__ import annotations

import asyncio
import logging
import random
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from agent_smith.app.ports.document_processing import (
    DocumentJobQueue,
    FileProcessingError,
    FileTypeDetector,
    PendingDerivative,
    ProcessingInput,
    ProcessorRegistry,
)
from agent_smith.app.ports.files import BlobStorageError, BlobStore, FileRecord
from agent_smith.infra.document_processing.serialization import (
    artifact_identity,
    build_core_artifacts,
)

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class DocumentProcessingConfig:
    max_file_bytes: int
    poll_seconds: float
    lease_seconds: int
    heartbeat_seconds: int
    timeout_seconds: int
    pipeline_version: str
    max_attempts: int
    reconciliation_seconds: float = 60.0


class DocumentProcessingWorker:
    def __init__(
        self,
        job_queue: DocumentJobQueue,
        blob_store: BlobStore,
        file_type_detector: FileTypeDetector,
        processor_registry: ProcessorRegistry,
        config: DocumentProcessingConfig,
        *,
        worker_id: str | None = None,
    ) -> None:
        self._queue = job_queue
        self._blobs = blob_store
        self._detector = file_type_detector
        self._processors = processor_registry
        self.config = config
        self.worker_id = worker_id or f"worker-{uuid.uuid4().hex[:12]}"

    async def run_forever(self, stop_event: asyncio.Event) -> None:
        last_reconciliation = datetime.min.replace(tzinfo=UTC)
        while not stop_event.is_set():
            try:
                if (
                    datetime.now(UTC) - last_reconciliation
                ).total_seconds() >= self.config.reconciliation_seconds:
                    await self._queue.reconcile_uploaded(
                        pipeline_version=self.config.pipeline_version,
                        max_attempts=self.config.max_attempts,
                    )
                    last_reconciliation = datetime.now(UTC)
                handled = await self.run_processing_once()
            except Exception:
                logger.exception("Document worker iteration failed")
                handled = False
            if handled:
                continue
            try:
                await asyncio.wait_for(stop_event.wait(), timeout=self.config.poll_seconds)
            except TimeoutError:
                pass

    async def run_processing_once(self) -> bool:
        claimed = await self._queue.claim_next(
            worker_id=self.worker_id,
            lease_seconds=self.config.lease_seconds,
        )
        if claimed is None:
            return False
        job, file = claimed
        heartbeat = asyncio.create_task(self._heartbeat(job.id))
        try:
            await asyncio.wait_for(
                self._process(job.id, job.pipeline_version, file),
                timeout=self.config.timeout_seconds,
            )
        except TimeoutError:
            await self._handle_error(
                job.id,
                job.attempts,
                job.max_attempts,
                FileProcessingError(
                    "processing_timeout",
                    "Document processing timed out.",
                    retryable=True,
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
                    "storage_unavailable",
                    "Object storage is temporarily unavailable.",
                    retryable=True,
                ),
            )
        except Exception:
            logger.exception("Unexpected document processor failure", extra={"job_id": job.id})
            await self._handle_error(
                job.id,
                job.attempts,
                job.max_attempts,
                FileProcessingError(
                    "processor_failure",
                    "Document processor failed unexpectedly.",
                    retryable=True,
                ),
            )
        finally:
            heartbeat.cancel()
            try:
                await heartbeat
            except asyncio.CancelledError:
                pass
        return True

    async def _process(
        self, job_id: str, pipeline_version: str, file: FileRecord
    ) -> None:
        data = await self._blobs.read_object(
            object_key=file.object_key,
            max_bytes=self.config.max_file_bytes,
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
        owned = await self._queue.set_detected_type(
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
            stat = await self._blobs.write_object(
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
        completed = await self._queue.complete_job(
            job_id=job_id,
            worker_id=self.worker_id,
            derivatives=pending,
            processing_metadata=metadata,
        )
        if not completed:
            raise FileProcessingError("lease_lost", "Processing lease was lost.", retryable=True)

    async def _heartbeat(self, job_id: str) -> None:
        while True:
            await asyncio.sleep(self.config.heartbeat_seconds)
            try:
                alive = await self._queue.heartbeat(
                    job_id=job_id,
                    worker_id=self.worker_id,
                    lease_seconds=self.config.lease_seconds,
                )
            except Exception:
                logger.exception("Document worker heartbeat failed", extra={"job_id": job_id})
                continue
            if not alive:
                return

    async def _progress(self, job_id: str, phase: str, percent: int) -> None:
        await self._queue.update_progress(
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
            await self._queue.fail_job(
                job_id=job_id, worker_id=self.worker_id, error=payload
            )
            return
        cap = min(300.0, 5.0 * (2 ** max(0, attempts - 1)))
        available_at = datetime.now(UTC) + timedelta(seconds=random.uniform(0, cap))
        logger.info(
            "Document processing scheduled for retry",
            extra={"job_id": job_id, "error_code": error.code, "attempts": attempts},
        )
        await self._queue.schedule_retry(
            job_id=job_id,
            worker_id=self.worker_id,
            error=payload,
            available_at=available_at,
        )


def _mime_compatible(declared: str, detected: str) -> bool:
    text_types = {"text/plain", "text/markdown", "text/csv"}
    return declared == detected or (declared in text_types and detected in text_types)
