"""Document-worker process composition root."""

from agent_smith.app.services.file_maintenance import FileMaintenanceService
from agent_smith.bootstrap.common import create_blob_store, create_postgres_runtime
from agent_smith.infra.config import Settings
from agent_smith.infra.document_processing import (
    SupportedFileTypeDetector,
    create_processor_registry,
)
from agent_smith.infra.storage.postgres import PostgresRuntime
from agent_smith.infra.storage.postgres.adapters import (
    PostgresDocumentJobQueue,
    PostgresFileMaintenanceStore,
)
from agent_smith.workers.document_processing.application import DocumentWorkerApplication
from agent_smith.workers.document_processing.maintenance import FileMaintenanceRunner
from agent_smith.workers.document_processing.worker import (
    DocumentProcessingConfig,
    DocumentProcessingWorker,
)


class DocumentWorkerContainer:
    def __init__(
        self,
        *,
        settings: Settings,
        worker: DocumentProcessingWorker,
        maintenance: FileMaintenanceRunner,
        application: DocumentWorkerApplication,
        postgres_runtime: PostgresRuntime,
    ) -> None:
        self.settings = settings
        self.worker = worker
        self.maintenance = maintenance
        self.application = application
        self._postgres_runtime = postgres_runtime

    async def close(self) -> None:
        await self.application.close()


def build_document_worker_container(settings: Settings) -> DocumentWorkerContainer:
    postgres = create_postgres_runtime(settings)
    blobs = create_blob_store(settings)
    worker = DocumentProcessingWorker(
        PostgresDocumentJobQueue(postgres.session_factory),
        blobs,
        SupportedFileTypeDetector(),
        create_processor_registry(),
        DocumentProcessingConfig(
            max_file_bytes=settings.file_max_bytes,
            poll_seconds=settings.file_processing_poll_seconds,
            lease_seconds=settings.file_processing_lease_seconds,
            heartbeat_seconds=settings.file_processing_heartbeat_seconds,
            timeout_seconds=settings.file_processing_timeout_seconds,
            pipeline_version=settings.file_processing_pipeline_version,
            max_attempts=settings.file_processing_max_attempts,
        ),
    )
    maintenance_service = FileMaintenanceService(
        PostgresFileMaintenanceStore(postgres.session_factory),
        blobs,
        pending_ttl_seconds=settings.file_pending_ttl_seconds,
        deleted_retention_seconds=settings.file_deleted_retention_seconds,
        audit_retention_seconds=settings.file_audit_retention_seconds,
    )
    maintenance = FileMaintenanceRunner(
        maintenance_service,
        interval_seconds=settings.file_maintenance_interval_seconds,
    )
    application = DocumentWorkerApplication(worker, maintenance, close=postgres.close)
    return DocumentWorkerContainer(
        settings=settings,
        worker=worker,
        maintenance=maintenance,
        application=application,
        postgres_runtime=postgres,
    )
