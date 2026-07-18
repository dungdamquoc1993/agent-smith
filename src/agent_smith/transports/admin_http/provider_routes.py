"""Authenticated identity-provider control-plane routes."""

from __future__ import annotations

from http import HTTPStatus
from typing import Annotated

from fastapi import APIRouter, Depends, Path, Query, Request

from agent_smith.app.ports.admin import AuthenticatedAdminSession
from agent_smith.bootstrap.admin_http import AdminHttpContainer
from agent_smith.transports.admin_http.security import (
    get_container,
    require_admin_mutation,
    require_admin_session,
)
from agent_smith.transports.shared_http import json_response, read_json_object

router = APIRouter(prefix="/api")


@router.get("/identity-providers")
async def list_identity_providers(
    limit: int = Query(default=50, ge=1, le=200),
    cursor: str | None = None,
    authenticated: AuthenticatedAdminSession = Depends(require_admin_session),
    container: AdminHttpContainer = Depends(get_container),
):
    del authenticated
    return json_response(
        await container.identity_provider_control.list_providers(limit=limit, cursor=cursor)
    )


@router.post("/identity-providers", status_code=int(HTTPStatus.CREATED))
async def create_identity_provider(
    request: Request,
    authenticated: AuthenticatedAdminSession = Depends(require_admin_mutation),
    container: AdminHttpContainer = Depends(get_container),
):
    return json_response(
        await container.identity_provider_control.create_provider(
            await read_json_object(request, invalid_status=HTTPStatus.UNPROCESSABLE_ENTITY),
            actor=authenticated.actor,
        ),
        status_code=HTTPStatus.CREATED,
    )


@router.get("/identity-providers/{providerId}")
async def get_identity_provider(
    provider_id: Annotated[str, Path(alias="providerId")],
    authenticated: AuthenticatedAdminSession = Depends(require_admin_session),
    container: AdminHttpContainer = Depends(get_container),
):
    del authenticated
    return json_response(await container.identity_provider_control.get_provider(provider_id))


@router.patch("/identity-providers/{providerId}")
async def update_identity_provider(
    provider_id: Annotated[str, Path(alias="providerId")],
    request: Request,
    authenticated: AuthenticatedAdminSession = Depends(require_admin_mutation),
    container: AdminHttpContainer = Depends(get_container),
):
    return json_response(
        await container.identity_provider_control.update_provider(
            provider_id,
            await read_json_object(request, invalid_status=HTTPStatus.UNPROCESSABLE_ENTITY),
            actor=authenticated.actor,
        )
    )


@router.get("/identity-providers/{providerId}/api-keys")
async def list_api_keys(
    provider_id: Annotated[str, Path(alias="providerId")],
    limit: int = Query(default=50, ge=1, le=200),
    cursor: str | None = None,
    authenticated: AuthenticatedAdminSession = Depends(require_admin_session),
    container: AdminHttpContainer = Depends(get_container),
):
    del authenticated
    return json_response(
        await container.identity_provider_control.list_api_keys(
            provider_id, limit=limit, cursor=cursor
        )
    )


@router.post("/identity-providers/{providerId}/api-keys", status_code=201)
async def create_api_key(
    provider_id: Annotated[str, Path(alias="providerId")],
    request: Request,
    authenticated: AuthenticatedAdminSession = Depends(require_admin_mutation),
    container: AdminHttpContainer = Depends(get_container),
):
    return json_response(
        await container.identity_provider_control.create_api_key(
            provider_id,
            await read_json_object(request, invalid_status=HTTPStatus.UNPROCESSABLE_ENTITY),
            actor=authenticated.actor,
        ),
        status_code=HTTPStatus.CREATED,
    )


@router.post("/identity-provider-api-keys/{keyId}/revoke")
async def revoke_api_key(
    key_id: Annotated[str, Path(alias="keyId")],
    authenticated: AuthenticatedAdminSession = Depends(require_admin_mutation),
    container: AdminHttpContainer = Depends(get_container),
):
    return json_response(
        await container.identity_provider_control.revoke_api_key(
            key_id, actor=authenticated.actor
        )
    )


@router.get("/identity-providers/{providerId}/assertion-keys")
async def list_assertion_keys(
    provider_id: Annotated[str, Path(alias="providerId")],
    limit: int = Query(default=50, ge=1, le=200),
    cursor: str | None = None,
    authenticated: AuthenticatedAdminSession = Depends(require_admin_session),
    container: AdminHttpContainer = Depends(get_container),
):
    del authenticated
    return json_response(
        await container.identity_provider_control.list_assertion_keys(
            provider_id, limit=limit, cursor=cursor
        )
    )


@router.post("/identity-providers/{providerId}/assertion-keys", status_code=201)
async def create_assertion_key(
    provider_id: Annotated[str, Path(alias="providerId")],
    request: Request,
    authenticated: AuthenticatedAdminSession = Depends(require_admin_mutation),
    container: AdminHttpContainer = Depends(get_container),
):
    return json_response(
        await container.identity_provider_control.create_assertion_key(
            provider_id,
            await read_json_object(request, invalid_status=HTTPStatus.UNPROCESSABLE_ENTITY),
            actor=authenticated.actor,
        ),
        status_code=HTTPStatus.CREATED,
    )


@router.post("/identity-provider-assertion-keys/{keyId}/revoke")
async def revoke_assertion_key(
    key_id: Annotated[str, Path(alias="keyId")],
    authenticated: AuthenticatedAdminSession = Depends(require_admin_mutation),
    container: AdminHttpContainer = Depends(get_container),
):
    return json_response(
        await container.identity_provider_control.revoke_assertion_key(
            key_id, actor=authenticated.actor
        )
    )
