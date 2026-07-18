"""Admin HTTP request context, session, Origin, and CSRF enforcement."""

from __future__ import annotations

import ipaddress
from dataclasses import dataclass, replace
from http import HTTPStatus

from fastapi import Depends, Request

from agent_smith.app.ports.admin import (
    AdminActorContext,
    AuthenticatedAdminSession,
)
from agent_smith.app.services.admin import AdminAuthenticationError
from agent_smith.bootstrap.admin_http import AdminHttpContainer
from agent_smith.transports.shared_http import AgentSmithHttpError, request_id_from_header

REQUEST_ID_HEADER = "X-Request-ID"
CSRF_HEADER = "X-CSRF-Token"


@dataclass(frozen=True)
class AdminRequestContext:
    request_id: str
    ip_address: str | None
    user_agent: str | None


def get_container(request: Request) -> AdminHttpContainer:
    return request.app.state.container


def build_request_context(request: Request, trusted_proxies: str) -> AdminRequestContext:
    request_id = request_id_from_header(request.headers.get(REQUEST_ID_HEADER))
    peer = request.client.host if request.client else None
    ip_address = _valid_ip(peer)
    if ip_address is not None and _is_trusted_proxy(ip_address, trusted_proxies):
        forwarded = request.headers.get("X-Forwarded-For", "").split(",", 1)[0].strip()
        ip_address = _valid_ip(forwarded) or ip_address
    user_agent = request.headers.get("User-Agent")
    return AdminRequestContext(
        request_id=request_id,
        ip_address=ip_address,
        user_agent=user_agent[:512] if user_agent else None,
    )


def get_request_context(request: Request) -> AdminRequestContext:
    return request.state.admin_request_context


def require_exact_origin(request: Request, container: AdminHttpContainer) -> None:
    if request.headers.get("Origin") != container.settings.public_origin:
        raise AgentSmithHttpError(
            HTTPStatus.FORBIDDEN,
            "admin_origin_denied",
            "Request origin is not allowed.",
        )


async def require_admin_session(
    request: Request,
    container: AdminHttpContainer = Depends(get_container),
    context: AdminRequestContext = Depends(get_request_context),
) -> AuthenticatedAdminSession:
    token = request.cookies.get(container.settings.session_cookie_name)
    if not token:
        await _audit_denial(container, context, request, "missing_session")
        raise _unauthorized()
    try:
        authenticated = await container.authentication.verify_session(token)
    except AdminAuthenticationError as exc:
        await _audit_denial(container, context, request, "invalid_session")
        raise _unauthorized() from exc
    actor = replace(
        authenticated.actor,
        request_id=context.request_id,
        ip_address=context.ip_address,
        user_agent=context.user_agent,
    )
    return replace(authenticated, actor=actor)


async def require_admin_mutation(
    request: Request,
    authenticated: AuthenticatedAdminSession = Depends(require_admin_session),
    container: AdminHttpContainer = Depends(get_container),
    context: AdminRequestContext = Depends(get_request_context),
) -> AuthenticatedAdminSession:
    try:
        require_exact_origin(request, container)
    except AgentSmithHttpError:
        await _audit_denial(
            container, context, request, "origin_mismatch", actor=authenticated.actor
        )
        raise
    csrf_cookie = request.cookies.get(container.settings.csrf_cookie_name, "")
    csrf_header = request.headers.get(CSRF_HEADER, "")
    if not container.authentication.verify_csrf(
        authenticated.session, csrf_cookie, csrf_header
    ):
        await _audit_denial(
            container, context, request, "csrf_mismatch", actor=authenticated.actor
        )
        raise AgentSmithHttpError(
            HTTPStatus.FORBIDDEN,
            "admin_csrf_denied",
            "CSRF verification failed.",
        )
    return authenticated


async def _audit_denial(
    container: AdminHttpContainer,
    context: AdminRequestContext,
    request: Request,
    reason: str,
    *,
    actor: AdminActorContext | None = None,
) -> None:
    resolved_actor = actor or AdminActorContext(
        kind="anonymous",
        request_id=context.request_id,
        ip_address=context.ip_address,
        user_agent=context.user_agent,
    )
    await container.authentication.audit_denial(
        action="admin.auth.request_denied",
        reason=reason,
        actor=resolved_actor,
    )


def _unauthorized() -> AgentSmithHttpError:
    return AgentSmithHttpError(
        HTTPStatus.UNAUTHORIZED,
        "admin_unauthorized",
        "Authentication is required.",
    )


def _valid_ip(value: str | None) -> str | None:
    if not value:
        return None
    try:
        return str(ipaddress.ip_address(value))
    except ValueError:
        return None


def _is_trusted_proxy(peer: str, configured: str) -> bool:
    address = ipaddress.ip_address(peer)
    for raw in configured.split(","):
        value = raw.strip()
        if not value:
            continue
        try:
            if address in ipaddress.ip_network(value, strict=False):
                return True
        except ValueError:
            continue
    return False
