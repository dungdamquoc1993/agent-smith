"""Admin HTTP routes for identity provider management."""

from __future__ import annotations

from http import HTTPStatus
from http.server import BaseHTTPRequestHandler
from typing import Any

from agent_smith.app.container import AppContainer
from agent_smith.app.services.identity_providers import IdentityProviderManagementError
from agent_smith.transports.http.common import require_admin_auth, send_error, send_json

ADMIN_IDENTITY_PROVIDER_ROUTES = [
    "/api/admin/identity-providers",
    "/api/admin/identity-providers/{providerId}",
    "/api/admin/identity-providers/{providerId}/api-keys",
    "/api/admin/identity-provider-api-keys/{keyId}/revoke",
    "/api/admin/identity-providers/{providerId}/assertion-keys",
    "/api/admin/identity-provider-assertion-keys/{keyId}/revoke",
]


def handle_admin_get(
    *,
    handler: BaseHTTPRequestHandler,
    path: str,
    container: AppContainer,
    runtime: Any,
) -> bool:
    if not path.startswith("/api/admin/"):
        return False
    if not require_admin_auth(handler, container):
        return True

    try:
        if path == "/api/admin/identity-providers":
            send_json(handler, runtime.run(container.identity_providers.list_providers()))
            return True

        provider_id, child = _provider_child(path)
        if provider_id and child is None:
            send_json(handler, runtime.run(container.identity_providers.get_provider(provider_id)))
            return True
        if provider_id and child == "api-keys":
            send_json(handler, runtime.run(container.identity_providers.list_api_keys(provider_id)))
            return True
        if provider_id and child == "assertion-keys":
            send_json(handler, runtime.run(container.identity_providers.list_assertion_keys(provider_id)))
            return True
    except IdentityProviderManagementError as exc:
        _send_management_error(handler, exc)
        return True

    send_error(handler, HTTPStatus.NOT_FOUND, "Not found")
    return True


def handle_admin_post(
    *,
    handler: BaseHTTPRequestHandler,
    path: str,
    body: dict[str, Any],
    container: AppContainer,
    runtime: Any,
) -> bool:
    if not path.startswith("/api/admin/"):
        return False
    if not require_admin_auth(handler, container):
        return True

    try:
        if path == "/api/admin/identity-providers":
            send_json(
                handler,
                runtime.run(container.identity_providers.create_provider(body)),
                status=HTTPStatus.CREATED,
            )
            return True

        provider_id, child = _provider_child(path)
        if provider_id and child == "api-keys":
            send_json(
                handler,
                runtime.run(container.identity_providers.create_api_key(provider_id, body)),
                status=HTTPStatus.CREATED,
            )
            return True
        if provider_id and child == "assertion-keys":
            send_json(
                handler,
                runtime.run(container.identity_providers.create_assertion_key(provider_id, body)),
                status=HTTPStatus.CREATED,
            )
            return True

        api_key_id = _revoke_id(path, "/api/admin/identity-provider-api-keys/")
        if api_key_id is not None:
            send_json(handler, runtime.run(container.identity_providers.revoke_api_key(api_key_id)))
            return True

        assertion_key_id = _revoke_id(path, "/api/admin/identity-provider-assertion-keys/")
        if assertion_key_id is not None:
            send_json(
                handler,
                runtime.run(container.identity_providers.revoke_assertion_key(assertion_key_id)),
            )
            return True
    except IdentityProviderManagementError as exc:
        _send_management_error(handler, exc)
        return True

    send_error(handler, HTTPStatus.NOT_FOUND, "Not found")
    return True


def handle_admin_patch(
    *,
    handler: BaseHTTPRequestHandler,
    path: str,
    body: dict[str, Any],
    container: AppContainer,
    runtime: Any,
) -> bool:
    if not path.startswith("/api/admin/"):
        return False
    if not require_admin_auth(handler, container):
        return True

    try:
        provider_id, child = _provider_child(path)
        if provider_id and child is None:
            send_json(handler, runtime.run(container.identity_providers.update_provider(provider_id, body)))
            return True
    except IdentityProviderManagementError as exc:
        _send_management_error(handler, exc)
        return True

    send_error(handler, HTTPStatus.NOT_FOUND, "Not found")
    return True


def _provider_child(path: str) -> tuple[str | None, str | None]:
    prefix = "/api/admin/identity-providers/"
    if not path.startswith(prefix):
        return None, None
    parts = path.removeprefix(prefix).strip("/").split("/")
    if len(parts) == 1 and parts[0]:
        return parts[0], None
    if len(parts) == 2 and parts[0] and parts[1] in {"api-keys", "assertion-keys"}:
        return parts[0], parts[1]
    return None, None


def _revoke_id(path: str, prefix: str) -> str | None:
    suffix = "/revoke"
    if not path.startswith(prefix) or not path.endswith(suffix):
        return None
    key_id = path.removeprefix(prefix).removesuffix(suffix).strip("/")
    return key_id or None


def _send_management_error(
    handler: BaseHTTPRequestHandler,
    exc: IdentityProviderManagementError,
) -> None:
    send_json(
        handler,
        {"error": {"code": exc.code, "message": exc.message}},
        status=exc.status,
    )
