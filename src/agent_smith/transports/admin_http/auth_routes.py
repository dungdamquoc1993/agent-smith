"""Admin sign-in, session inspection, and sign-out routes."""

from __future__ import annotations

from datetime import timedelta
from http import HTTPStatus

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, ConfigDict, Field

from agent_smith.app.ports.admin import AuthenticatedAdminSession
from agent_smith.app.services.admin import AdminAuthenticationError, SESSION_ABSOLUTE_TTL
from agent_smith.bootstrap.admin_http import AdminHttpContainer
from agent_smith.transports.admin_http.security import (
    AdminRequestContext,
    get_container,
    get_request_context,
    require_admin_mutation,
    require_admin_session,
    require_exact_origin,
)
from agent_smith.transports.shared_http import AgentSmithHttpError, json_response

router = APIRouter(prefix="/auth")


class SignInBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    username: str = Field(min_length=1, max_length=128)
    password: str = Field(min_length=1, max_length=4096)


@router.post("/sign-in")
async def sign_in(
    body: SignInBody,
    request: Request,
    container: AdminHttpContainer = Depends(get_container),
    context: AdminRequestContext = Depends(get_request_context),
):
    try:
        require_exact_origin(request, container)
    except AgentSmithHttpError:
        await container.authentication.audit_denial(
            action="admin.auth.sign_in",
            reason="origin_mismatch",
            actor=_anonymous_actor(context),
        )
        raise
    try:
        created = await container.authentication.sign_in(
            username=body.username,
            password=body.password,
            ip_address=context.ip_address,
            user_agent=context.user_agent,
            request_id=context.request_id,
        )
    except AdminAuthenticationError as exc:
        raise AgentSmithHttpError(
            HTTPStatus.UNAUTHORIZED,
            "invalid_credentials",
            "Invalid username or password.",
        ) from exc

    response = json_response(_session_payload(created.operator, created.session))
    _set_auth_cookies(
        response,
        container,
        session_token=created.session_token,
        csrf_token=created.csrf_token,
        expires=created.session.absolute_expires_at,
    )
    return response


@router.get("/session")
async def get_session(
    authenticated: AuthenticatedAdminSession = Depends(require_admin_session),
):
    return json_response(_session_payload(authenticated.operator, authenticated.session))


@router.post("/sign-out")
async def sign_out(
    request: Request,
    authenticated: AuthenticatedAdminSession = Depends(require_admin_mutation),
    container: AdminHttpContainer = Depends(get_container),
    context: AdminRequestContext = Depends(get_request_context),
):
    del authenticated
    token = request.cookies.get(container.settings.session_cookie_name, "")
    await container.authentication.sign_out(token, request_id=context.request_id)
    response = json_response({"signedOut": True})
    _clear_auth_cookies(response, container)
    return response


def _session_payload(operator: object, session: object) -> dict[str, object]:
    return {
        "operator": {
            "id": getattr(operator, "id"),
            "username": getattr(operator, "username"),
            "displayName": getattr(operator, "display_name"),
            "status": getattr(operator, "status"),
        },
        "session": {
            "idleExpiresAt": getattr(session, "idle_expires_at"),
            "absoluteExpiresAt": getattr(session, "absolute_expires_at"),
        },
    }


def _set_auth_cookies(
    response: object,
    container: AdminHttpContainer,
    *,
    session_token: str,
    csrf_token: str,
    expires: object,
) -> None:
    common = {
        "max_age": int(SESSION_ABSOLUTE_TTL / timedelta(seconds=1)),
        "expires": expires,
        "path": "/",
        "secure": container.settings.cookie_secure,
        "samesite": "strict",
    }
    response.set_cookie(  # type: ignore[attr-defined]
        container.settings.session_cookie_name,
        session_token,
        httponly=True,
        **common,
    )
    response.set_cookie(  # type: ignore[attr-defined]
        container.settings.csrf_cookie_name,
        csrf_token,
        httponly=False,
        **common,
    )


def _clear_auth_cookies(response: object, container: AdminHttpContainer) -> None:
    response.delete_cookie(  # type: ignore[attr-defined]
        container.settings.session_cookie_name,
        path="/",
        secure=container.settings.cookie_secure,
        httponly=True,
        samesite="strict",
    )
    response.delete_cookie(  # type: ignore[attr-defined]
        container.settings.csrf_cookie_name,
        path="/",
        secure=container.settings.cookie_secure,
        httponly=False,
        samesite="strict",
    )


def _anonymous_actor(context: AdminRequestContext):
    from agent_smith.app.ports.admin import AdminActorContext

    return AdminActorContext(
        kind="anonymous",
        request_id=context.request_id,
        ip_address=context.ip_address,
        user_agent=context.user_agent,
    )
