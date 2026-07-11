"""Admin FastAPI routes for identity provider management."""

from __future__ import annotations

from http import HTTPStatus

from fastapi import APIRouter, Depends, Request

from agent_smith.app.container import AppContainer
from agent_smith.transports.http.common import (
    get_container,
    json_response,
    read_json_object,
    require_admin_token,
)

ADMIN_IDENTITY_PROVIDER_ROUTES = [
    "/api/admin/identity-providers",
    "/api/admin/identity-providers/{providerId}",
    "/api/admin/identity-providers/{providerId}/api-keys",
    "/api/admin/identity-provider-api-keys/{keyId}/revoke",
    "/api/admin/identity-providers/{providerId}/assertion-keys",
    "/api/admin/identity-provider-assertion-keys/{keyId}/revoke",
]

router = APIRouter(
    prefix="/api/admin",
    dependencies=[Depends(require_admin_token)],
)


@router.get("/identity-providers")
async def list_identity_providers(container: AppContainer = Depends(get_container)):
    return json_response(await container.identity_providers.list_providers())


@router.post("/identity-providers", status_code=int(HTTPStatus.CREATED))
async def create_identity_provider(
    request: Request,
    container: AppContainer = Depends(get_container),
):
    body = await read_json_object(request)
    return json_response(
        await container.identity_providers.create_provider(body),
        status_code=HTTPStatus.CREATED,
    )


@router.get("/identity-providers/{provider_id}")
async def get_identity_provider(
    provider_id: str,
    container: AppContainer = Depends(get_container),
):
    return json_response(await container.identity_providers.get_provider(provider_id))


@router.patch("/identity-providers/{provider_id}")
async def update_identity_provider(
    provider_id: str,
    request: Request,
    container: AppContainer = Depends(get_container),
):
    body = await read_json_object(request)
    return json_response(await container.identity_providers.update_provider(provider_id, body))


@router.get("/identity-providers/{provider_id}/api-keys")
async def list_provider_api_keys(
    provider_id: str,
    container: AppContainer = Depends(get_container),
):
    return json_response(await container.identity_providers.list_api_keys(provider_id))


@router.post("/identity-providers/{provider_id}/api-keys", status_code=int(HTTPStatus.CREATED))
async def create_provider_api_key(
    provider_id: str,
    request: Request,
    container: AppContainer = Depends(get_container),
):
    body = await read_json_object(request)
    return json_response(
        await container.identity_providers.create_api_key(provider_id, body),
        status_code=HTTPStatus.CREATED,
    )


@router.post("/identity-provider-api-keys/{key_id}/revoke")
async def revoke_provider_api_key(
    key_id: str,
    container: AppContainer = Depends(get_container),
):
    return json_response(await container.identity_providers.revoke_api_key(key_id))


@router.get("/identity-providers/{provider_id}/assertion-keys")
async def list_provider_assertion_keys(
    provider_id: str,
    container: AppContainer = Depends(get_container),
):
    return json_response(await container.identity_providers.list_assertion_keys(provider_id))


@router.post("/identity-providers/{provider_id}/assertion-keys", status_code=int(HTTPStatus.CREATED))
async def create_provider_assertion_key(
    provider_id: str,
    request: Request,
    container: AppContainer = Depends(get_container),
):
    body = await read_json_object(request)
    return json_response(
        await container.identity_providers.create_assertion_key(provider_id, body),
        status_code=HTTPStatus.CREATED,
    )


@router.post("/identity-provider-assertion-keys/{key_id}/revoke")
async def revoke_provider_assertion_key(
    key_id: str,
    container: AppContainer = Depends(get_container),
):
    return json_response(await container.identity_providers.revoke_assertion_key(key_id))
