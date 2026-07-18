from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi.testclient import TestClient

import agent_smith.bootstrap.runtime_http as http_bootstrap
import agent_smith.transports.runtime_http.main as http_main
from agent_smith.app.services.runtime import RuntimeService
from agent_smith.admin.config import AdminHttpSettings
from agent_smith.bootstrap.admin_http import AdminHttpContainer
from agent_smith.bootstrap.runtime_http import RuntimeHttpContainer
from agent_smith.bootstrap.document_worker import build_document_worker_container
from agent_smith.infra.config import RuntimeSettings
from agent_smith.infra.storage.postgres.database import Base
from agent_smith.transports.runtime_http.main import create_app
from agent_smith.workers.document_processing.application import DocumentWorkerApplication
from agent_smith.workers.document_processing.maintenance import FileMaintenanceRunner


@pytest.mark.asyncio
async def test_http_builder_bootstraps_llm_while_worker_builder_stays_independent(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[str] = []
    monkeypatch.setattr(http_bootstrap, "bootstrap_providers", lambda: calls.append("http"))
    settings = RuntimeSettings(_env_file=None)

    http = http_bootstrap.build_runtime_http_container(settings)
    worker = build_document_worker_container(settings)
    try:
        assert calls == ["http"]
        assert not hasattr(worker, "authentication")
        assert not hasattr(worker, "sessions")
        assert not hasattr(worker, "resources")
        assert not hasattr(worker, "agent_runs")
        assert http._postgres_runtime is not worker._postgres_runtime
    finally:
        await http.close()
        await worker.close()


def test_injected_http_container_remains_caller_owned() -> None:
    container = SimpleNamespace(
        settings=SimpleNamespace(http_docs_enabled=True),
        close=AsyncMock(),
    )

    with TestClient(create_app(container=container)):
        pass

    container.close.assert_not_awaited()


def test_app_owned_http_container_is_closed_on_shutdown(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = SimpleNamespace(http_docs_enabled=True)
    container = SimpleNamespace(settings=settings, close=AsyncMock())
    monkeypatch.setattr(http_main, "get_runtime_settings", lambda: settings)
    monkeypatch.setattr(http_main, "build_runtime_http_container", lambda _settings: container)

    with TestClient(http_main.create_app()):
        pass

    container.close.assert_awaited_once()


@pytest.mark.asyncio
async def test_runtime_and_admin_http_containers_dispose_postgres_once() -> None:
    runtime_postgres = SimpleNamespace(close=AsyncMock())
    admin_postgres = SimpleNamespace(close=AsyncMock())
    runtime = RuntimeHttpContainer(
        settings=RuntimeSettings(_env_file=None),
        runtime=MagicMock(),
        sessions=MagicMock(),
        authentication=MagicMock(),
        resources=MagicMock(),
        files=MagicMock(),
        agent_runs=MagicMock(),
        postgres_runtime=runtime_postgres,  # type: ignore[arg-type]
    )
    admin = AdminHttpContainer(
        settings=AdminHttpSettings(_env_file=None),
        authentication=MagicMock(),
        identity_provider_control=MagicMock(),
        audit_reader=MagicMock(),
        postgres_runtime=admin_postgres,  # type: ignore[arg-type]
    )

    await runtime.close()
    await runtime.close()
    await admin.close()
    await admin.close()

    runtime_postgres.close.assert_awaited_once()
    admin_postgres.close.assert_awaited_once()


@pytest.mark.asyncio
async def test_runtime_service_preserves_bootstrap_and_model_catalog_payloads() -> None:
    readiness = AsyncMock()
    readiness.check = AsyncMock()
    sessions = AsyncMock()
    sessions.ensure_principal.return_value = SimpleNamespace(
        id="principal-1",
        display_name="Principal",
        status="active",
        created_at=None,
        updated_at=None,
    )
    sessions.list_sessions.return_value = [{"id": "session-1"}]
    resources = AsyncMock()
    resources.default_agent_name = "assistant"
    resources.list_resources.return_value = {"resources": [{"name": "assistant"}]}
    agent_runs = MagicMock()
    agent_runs.default_model_selection.return_value = "model-a"
    agent_runs.model_choices.return_value = [{"key": "model-a"}]
    service = RuntimeService(
        readiness,
        sessions,
        resources,
        agent_runs,
        postgres_url="postgresql+asyncpg://example/runtime",
    )

    payload = await service.bootstrap()

    readiness.check.assert_awaited_once()
    assert payload["postgres"] == {
        "ok": True,
        "url": "postgresql+asyncpg://example/runtime",
    }
    assert payload["defaults"] == {"agentName": "assistant", "modelKey": "model-a"}
    assert payload["models"] == [{"key": "model-a"}]


@pytest.mark.asyncio
async def test_maintenance_failure_does_not_stop_document_loop_and_runtime_closes_once() -> None:
    started = asyncio.Event()

    class DocumentLoop:
        async def run_forever(self, stop_event: asyncio.Event) -> None:
            started.set()
            await stop_event.wait()

    class FailingMaintenance:
        async def cleanup_stale_uploads(self, *, limit: int) -> int:
            del limit
            raise RuntimeError("maintenance unavailable")

        async def cleanup_rejected_uploads(self, *, limit: int) -> int:
            return limit

        async def cleanup_deleted_files(self, *, limit: int) -> int:
            return limit

        async def cleanup_audit_events(self, *, limit: int) -> int:
            return limit

    close = AsyncMock()
    application = DocumentWorkerApplication(
        DocumentLoop(),  # type: ignore[arg-type]
        FileMaintenanceRunner(
            FailingMaintenance(),  # type: ignore[arg-type]
            interval_seconds=0.01,
        ),
        close=close,
    )

    task = asyncio.create_task(application.run())
    await asyncio.wait_for(started.wait(), timeout=1)
    await asyncio.sleep(0.03)
    assert not task.done()
    application.stop()
    await asyncio.wait_for(task, timeout=1)
    await application.close()
    close.assert_awaited_once()


def test_importing_postgres_models_populates_the_complete_schema() -> None:
    import agent_smith.infra.storage.postgres.models  # noqa: F401

    assert set(Base.metadata.tables) == {
        "admin_audit_events",
        "admin_operators",
        "admin_sessions",
        "app_assertion_nonces",
        "external_identities",
        "file_audit_events",
        "file_derivatives",
        "file_processing_jobs",
        "files",
        "identity_provider_api_keys",
        "identity_provider_assertion_keys",
        "identity_providers",
        "mcp_credentials",
        "principals",
        "resource_versions",
        "resources",
        "session_entries",
        "session_entry_files",
        "sessions",
    }
