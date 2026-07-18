from __future__ import annotations

from types import SimpleNamespace

from fastapi.testclient import TestClient

from agent_smith.app.auth import AppAssertionError
from agent_smith.app.services.files import FileService
from agent_smith.transports.runtime_http.main import create_app
from helpers.files import FakeBlobStore, FakeFileCatalog, FakeFileProcessingRepository


class _Authentication:
    async def authenticate(self, *, provider_api_key: str | None, authorization: str | None):
        if not provider_api_key or authorization != "Bearer assertion":
            raise AppAssertionError("missing_assertion", "Missing or invalid authentication.")
        return SimpleNamespace(principal_id=f"principal-{provider_api_key}")


def _client() -> tuple[TestClient, FakeFileCatalog]:
    catalog = FakeFileCatalog()
    container = SimpleNamespace(
        settings=SimpleNamespace(http_docs_enabled=True),
        authentication=_Authentication(),
        files=FileService(
            catalog,
            FakeBlobStore(),
            max_bytes=1024,
            presign_ttl_seconds=900,
            max_pending_uploads=100,
        ),
    )
    return TestClient(create_app(container=container)), catalog


def _headers(principal: str) -> dict[str, str]:
    return {
        "X-Agent-Smith-Provider-Key": principal,
        "Authorization": "Bearer assertion",
    }


def test_file_routes_require_partner_authentication() -> None:
    client, _ = _client()
    with client:
        response = client.get("/api/files")
    assert response.status_code == 401
    assert response.json()["error"]["code"] == "missing_assertion"


def test_upload_list_and_cross_principal_access() -> None:
    client, _ = _client()
    with client:
        created = client.post(
            "/api/files/uploads",
            headers=_headers("a"),
            json={
                "originalName": "notes.md",
                "mimeType": "text/markdown",
                "sizeBytes": 5,
            },
        )
        listed = client.get("/api/files", headers=_headers("a"))
        file_id = created.json()["file"]["id"]
        hidden = client.get(f"/api/files/{file_id}", headers=_headers("b"))
        hidden_delete = client.delete(f"/api/files/{file_id}", headers=_headers("b"))

    assert created.status_code == 201
    assert created.json()["upload"]["method"] == "PUT"
    assert listed.status_code == 200
    assert [file["id"] for file in listed.json()["files"]] == [file_id]
    assert listed.json()["files"][0]["detectedMimeType"] is None
    assert listed.json()["files"][0]["processing"] is None
    assert listed.json()["files"][0]["processingMetadata"] == {}
    assert hidden.status_code == 404
    assert hidden_delete.status_code == 404


def test_file_route_rejects_large_file_and_has_no_binary_proxy() -> None:
    client, _ = _client()
    with client:
        response = client.post(
            "/api/files/uploads",
            headers=_headers("a"),
            json={
                "originalName": "large.pdf",
                "mimeType": "application/pdf",
                "sizeBytes": 1025,
            },
        )
        paths = set(client.app.openapi()["paths"])

    assert response.status_code == 413
    assert response.json()["error"]["code"] == "file_too_large"
    assert "/api/files/upload-binary" not in paths


def test_file_route_rejects_legacy_document_before_presigning() -> None:
    client, _ = _client()
    with client:
        response = client.post(
            "/api/files/uploads",
            headers=_headers("a"),
            json={
                "originalName": "legacy.doc",
                "mimeType": "application/msword",
                "sizeBytes": 10,
            },
        )

    assert response.status_code == 415
    assert response.json()["error"]["code"] == "unsupported_file_type"


def test_file_route_exposes_durable_processing_progress() -> None:
    catalog, blobs = FakeFileCatalog(), FakeBlobStore()
    processing = FakeFileProcessingRepository(catalog)
    container = SimpleNamespace(
        settings=SimpleNamespace(http_docs_enabled=True),
        authentication=_Authentication(),
        files=FileService(
            catalog,
            blobs,
            max_bytes=1024,
            presign_ttl_seconds=900,
            processing_repository=processing,
        ),
    )
    client = TestClient(create_app(container=container))
    with client:
        initiated = client.post(
            "/api/files/uploads",
            headers=_headers("a"),
            json={"originalName": "a.txt", "mimeType": "text/plain", "sizeBytes": 5},
        )
        file_id = initiated.json()["file"]["id"]
        blobs.upload(catalog.records[file_id], b"hello")
        completed = client.post(
            f"/api/files/{file_id}/complete", headers=_headers("a")
        )
        fetched = client.get(f"/api/files/{file_id}", headers=_headers("a"))

    for response in (completed, fetched):
        processing_payload = response.json()["file"]["processing"]
        assert processing_payload["status"] == "queued"
        assert processing_payload["phase"] == "queued"
        assert processing_payload["progressPercent"] == 0
        assert processing_payload["attempts"] == 0
        assert processing_payload["maxAttempts"] == 5


def test_file_route_pagination_filter_and_deleted_download() -> None:
    client, _ = _client()
    with client:
        created_ids = []
        for name, mime_type in (
            ("a.txt", "text/plain"),
            ("b.txt", "text/plain"),
            ("c.md", "text/markdown"),
        ):
            response = client.post(
                "/api/files/uploads",
                headers=_headers("a"),
                json={"originalName": name, "mimeType": mime_type, "sizeBytes": 1},
            )
            created_ids.append(response.json()["file"]["id"])
        first = client.get("/api/files?limit=2", headers=_headers("a"))
        cursor = first.json()["nextCursor"]
        second = client.get(f"/api/files?limit=2&cursor={cursor}", headers=_headers("a"))
        filtered = client.get(
            "/api/files?mimeType=text/markdown&status=pending_upload",
            headers=_headers("a"),
        )
        deleted = client.delete(f"/api/files/{created_ids[0]}", headers=_headers("a"))
        download = client.post(
            f"/api/files/{created_ids[0]}/download-url",
            headers=_headers("a"),
        )

    assert len(first.json()["files"]) == 2
    assert len(second.json()["files"]) == 1
    assert [file["originalName"] for file in filtered.json()["files"]] == ["c.md"]
    assert deleted.status_code == 204
    assert download.status_code == 404


def test_file_route_rate_limit_has_retry_after_and_no_authentication_secret() -> None:
    client, _ = _client()
    with client:
        responses = [
            client.post(
                "/api/files/uploads",
                headers=_headers("a"),
                json={"originalName": f"{index}.txt", "mimeType": "text/plain", "sizeBytes": 1},
            )
            for index in range(31)
        ]
        other_principal = client.post(
            "/api/files/uploads",
            headers=_headers("b"),
            json={"originalName": "b.txt", "mimeType": "text/plain", "sizeBytes": 1},
        )

    limited = responses[-1]
    assert all(response.status_code == 201 for response in responses[:-1])
    assert limited.status_code == 429
    assert limited.json()["error"]["code"] == "rate_limited"
    assert int(limited.headers["Retry-After"]) >= 1
    assert "assertion" not in limited.text
    assert other_principal.status_code == 201
