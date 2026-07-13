"""Authenticated managed-file library HTTP API."""

from __future__ import annotations

from http import HTTPStatus
from typing import Any

from fastapi import APIRouter, Depends, Header, Query, Request, Response
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from agent_smith.app.auth import AppAssertionError
from agent_smith.app.container import AppContainer
from agent_smith.app.ports.files import FileRecord, PresignedRequest
from agent_smith.app.services.authentication import AuthenticatedPrincipal
from agent_smith.app.services.files import FileServiceError
from agent_smith.transports.http.common import (
    AgentSmithHttpError,
    get_container,
    json_response,
    read_json_object,
)

FILE_ROUTES = [
    "/api/files/uploads",
    "/api/files/{fileId}/complete",
    "/api/files",
    "/api/files/{fileId}",
    "/api/files/{fileId}/download-url",
]

router = APIRouter()


class InitiateUploadBody(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    original_name: str = Field(alias="originalName")
    mime_type: str = Field(alias="mimeType")
    size_bytes: int = Field(alias="sizeBytes")
    sha256: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


async def authenticate_principal(
    provider_api_key: str | None = Header(default=None, alias="X-Agent-Smith-Provider-Key"),
    authorization: str | None = Header(default=None, alias="Authorization"),
    container: AppContainer = Depends(get_container),
) -> AuthenticatedPrincipal:
    try:
        return await container.authentication.authenticate(
            provider_api_key=provider_api_key,
            authorization=authorization,
        )
    except AppAssertionError as exc:
        raise AgentSmithHttpError(HTTPStatus.UNAUTHORIZED, exc.code, exc.message) from exc


@router.post("/api/files/uploads", status_code=int(HTTPStatus.CREATED))
async def initiate_upload(
    request: Request,
    principal: AuthenticatedPrincipal = Depends(authenticate_principal),
    container: AppContainer = Depends(get_container),
):
    try:
        body = InitiateUploadBody.model_validate(await read_json_object(request))
        result = await container.files.initiate_upload(
            principal_id=principal.principal_id,
            original_name=body.original_name,
            mime_type=body.mime_type,
            size_bytes=body.size_bytes,
            sha256=body.sha256,
            metadata=body.metadata,
        )
    except ValidationError as exc:
        raise AgentSmithHttpError(HTTPStatus.BAD_REQUEST, "invalid_file", str(exc)) from exc
    except FileServiceError as exc:
        raise _http_file_error(exc) from exc
    return json_response(
        {"file": _file_payload(result.file), "upload": _presign_payload(result.upload)},
        status_code=HTTPStatus.CREATED,
    )


@router.post("/api/files/{file_id}/complete")
async def complete_upload(
    file_id: str,
    principal: AuthenticatedPrincipal = Depends(authenticate_principal),
    container: AppContainer = Depends(get_container),
):
    try:
        file = await container.files.complete_upload(
            principal_id=principal.principal_id,
            file_id=file_id,
        )
    except FileServiceError as exc:
        raise _http_file_error(exc) from exc
    return json_response({"file": _file_payload(file)})


@router.get("/api/files")
async def list_files(
    limit: int = Query(default=50),
    cursor: str | None = None,
    status: str | None = None,
    mime_type: str | None = Query(default=None, alias="mimeType"),
    principal: AuthenticatedPrincipal = Depends(authenticate_principal),
    container: AppContainer = Depends(get_container),
):
    valid_statuses = {
        "pending_upload",
        "uploaded",
        "processing",
        "ready",
        "failed",
        "deleted",
    }
    if status is not None and status not in valid_statuses:
        raise AgentSmithHttpError(HTTPStatus.BAD_REQUEST, "invalid_file", "Invalid status.")
    try:
        page = await container.files.list_files(
            principal_id=principal.principal_id,
            limit=limit,
            cursor=cursor,
            status=status,  # type: ignore[arg-type]
            mime_type=mime_type,
        )
    except FileServiceError as exc:
        raise _http_file_error(exc) from exc
    return json_response(
        {
            "files": [_file_payload(file) for file in page.files],
            "nextCursor": page.next_cursor,
        }
    )


@router.get("/api/files/{file_id}")
async def get_file(
    file_id: str,
    principal: AuthenticatedPrincipal = Depends(authenticate_principal),
    container: AppContainer = Depends(get_container),
):
    try:
        file = await container.files.get_file(
            principal_id=principal.principal_id,
            file_id=file_id,
        )
    except FileServiceError as exc:
        raise _http_file_error(exc) from exc
    return json_response({"file": _file_payload(file)})


@router.post("/api/files/{file_id}/download-url")
async def create_download_url(
    file_id: str,
    principal: AuthenticatedPrincipal = Depends(authenticate_principal),
    container: AppContainer = Depends(get_container),
):
    try:
        download = await container.files.create_download_url(
            principal_id=principal.principal_id,
            file_id=file_id,
        )
    except FileServiceError as exc:
        raise _http_file_error(exc) from exc
    return json_response({"download": _presign_payload(download)})


@router.delete("/api/files/{file_id}", status_code=int(HTTPStatus.NO_CONTENT))
async def delete_file(
    file_id: str,
    principal: AuthenticatedPrincipal = Depends(authenticate_principal),
    container: AppContainer = Depends(get_container),
):
    try:
        await container.files.delete_file(
            principal_id=principal.principal_id,
            file_id=file_id,
        )
    except FileServiceError as exc:
        raise _http_file_error(exc) from exc
    return Response(status_code=HTTPStatus.NO_CONTENT)


def _file_payload(file: FileRecord) -> dict[str, Any]:
    return {
        "id": file.id,
        "originalName": file.original_name,
        "mimeType": file.mime_type,
        "sizeBytes": file.size_bytes,
        "sha256": file.sha256,
        "status": file.status,
        "etag": file.etag,
        "failureReason": file.failure_reason,
        "metadata": file.metadata,
        "createdAt": file.created_at,
        "updatedAt": file.updated_at,
        "deletedAt": file.deleted_at,
    }


def _presign_payload(request: PresignedRequest) -> dict[str, Any]:
    return {
        "url": request.url,
        "method": request.method,
        "headers": request.headers,
        "expiresAt": request.expires_at,
    }


def _http_file_error(exc: FileServiceError) -> AgentSmithHttpError:
    return AgentSmithHttpError(exc.status, exc.code, exc.message)
