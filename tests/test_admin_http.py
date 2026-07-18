from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError
from starlette.requests import Request

from agent_smith.admin.config import AdminHttpSettings
from agent_smith.app.ports.admin import (
    AdminActorContext,
    AdminOperatorRecord,
    AdminSessionRecord,
    AuthenticatedAdminSession,
    CreatedAdminSession,
)
from agent_smith.app.services.admin import AdminAuthenticationError, hash_admin_token
from agent_smith.transports.admin_http.main import create_app
from agent_smith.transports.admin_http.security import build_request_context

ORIGIN = "http://127.0.0.1:5174"
NOW = datetime(2026, 7, 18, tzinfo=UTC)


def _operator(*, status: str = "active") -> AdminOperatorRecord:
    return AdminOperatorRecord(
        id=str(uuid.uuid4()),
        username="admin",
        display_name="Admin User",
        password_hash="not-exposed",
        status=status,  # type: ignore[arg-type]
        created_at=NOW,
        updated_at=NOW,
    )


def _session(*, revoked_at: datetime | None = None) -> AdminSessionRecord:
    return AdminSessionRecord(
        id=str(uuid.uuid4()),
        operator_id="operator-id",
        token_hash=hash_admin_token("session-token"),
        csrf_token_hash=hash_admin_token("csrf-token"),
        created_at=NOW,
        last_seen_at=NOW,
        idle_expires_at=NOW + timedelta(hours=24),
        absolute_expires_at=NOW + timedelta(days=7),
        revoked_at=revoked_at,
    )


class FakeAuthentication:
    def __init__(self, *, sign_in_error: bool = False) -> None:
        self.operator = _operator()
        self.session = _session()
        self.sign_in_error = sign_in_error
        self.denials: list[tuple[str, str, AdminActorContext]] = []
        self.signed_out = False

    async def sign_in(self, **values: Any) -> CreatedAdminSession:
        if self.sign_in_error or values["password"] != "correct horse":
            raise AdminAuthenticationError("Invalid username or password.")
        return CreatedAdminSession(
            operator=self.operator,
            session=self.session,
            session_token="session-token",
            csrf_token="csrf-token",
        )

    async def verify_session(self, token: str, *, touch: bool = True) -> AuthenticatedAdminSession:
        del touch
        if token != "session-token" or self.signed_out:
            raise AdminAuthenticationError("Invalid admin session.")
        return AuthenticatedAdminSession(
            operator=self.operator,
            session=self.session,
            actor=AdminActorContext(
                kind="admin_operator",
                identifier=self.operator.username,
                operator_id=self.operator.id,
                session_id=self.session.id,
            ),
        )

    @staticmethod
    def verify_csrf(session: AdminSessionRecord, cookie: str, header: str) -> bool:
        return (
            cookie == header
            and hash_admin_token(header) == session.csrf_token_hash
        )

    async def audit_denial(
        self, *, action: str, reason: str, actor: AdminActorContext
    ) -> None:
        self.denials.append((action, reason, actor))

    async def sign_out(self, token: str, *, request_id: str | None = None) -> None:
        assert token == "session-token"
        assert request_id is not None
        self.signed_out = True


class FakeControl:
    def __init__(self) -> None:
        self.actor: AdminActorContext | None = None

    async def list_providers(self, **values: Any) -> dict[str, Any]:
        return {"identityProviders": [], "nextCursor": None, "query": values}

    async def get_provider(self, provider_id: str) -> dict[str, Any]:
        return {"identityProvider": {"id": provider_id}}

    async def create_provider(
        self, payload: dict[str, Any], *, actor: AdminActorContext
    ) -> dict[str, Any]:
        self.actor = actor
        return {"identityProvider": {"id": str(uuid.uuid4()), **payload}}


class FakeAuditReader:
    async def list_audit_events(self, **values: Any) -> list[Any]:
        del values
        return []


def _container(
    *,
    authentication: FakeAuthentication | None = None,
    settings: AdminHttpSettings | None = None,
) -> SimpleNamespace:
    return SimpleNamespace(
        settings=settings or AdminHttpSettings(_env_file=None),
        authentication=authentication or FakeAuthentication(),
        identity_provider_control=FakeControl(),
        audit_reader=FakeAuditReader(),
    )


def _sign_in(client: TestClient):
    return client.post(
        "/auth/sign-in",
        headers={"Origin": ORIGIN},
        json={"username": "admin", "password": "correct horse"},
    )


def test_admin_settings_cookie_names_and_origin_validation() -> None:
    development = AdminHttpSettings(_env_file=None)
    production = AdminHttpSettings(
        _env_file=None,
        public_origin="https://admin.example.com",
        cookie_secure=True,
    )
    assert development.session_cookie_name == "agent_smith_admin_session"
    assert development.csrf_cookie_name == "agent_smith_admin_csrf"
    assert development.public_origin == ORIGIN
    assert development.admin_ui_dist is None
    assert production.session_cookie_name == "__Host-agent_smith_admin_session"
    assert production.csrf_cookie_name == "__Host-agent_smith_admin_csrf"
    with pytest.raises(ValidationError):
        AdminHttpSettings(_env_file=None, public_origin="https://admin.example.com/path")
    with pytest.raises(ValidationError):
        AdminHttpSettings(_env_file=None, public_origin=ORIGIN, cookie_secure=True)
    with pytest.raises(ValidationError):
        AdminHttpSettings(_env_file=None, trusted_proxies="not-a-network")


def test_sign_in_session_and_sign_out_cookie_contract() -> None:
    container = _container()
    with TestClient(create_app(container=container)) as client:
        signed_in = _sign_in(client)
        assert signed_in.status_code == 200
        payload = signed_in.json()
        assert payload["operator"]["username"] == "admin"
        assert "session-token" not in signed_in.text
        assert "csrf-token" not in signed_in.text
        cookies = signed_in.headers.get_list("set-cookie")
        assert any("agent_smith_admin_session=" in value and "HttpOnly" in value for value in cookies)
        assert any("agent_smith_admin_csrf=" in value and "HttpOnly" not in value for value in cookies)
        assert all("SameSite=strict" in value and "Path=/" in value for value in cookies)
        assert all("Max-Age=604800" in value for value in cookies)

        session = client.get("/auth/session")
        assert session.status_code == 200
        assert session.json()["session"]["absoluteExpiresAt"]

        signed_out = client.post(
            "/auth/sign-out",
            headers={"Origin": ORIGIN, "X-CSRF-Token": "csrf-token"},
        )
        assert signed_out.status_code == 200
        assert signed_out.json() == {"signedOut": True}
        assert all("Max-Age=0" in value for value in signed_out.headers.get_list("set-cookie"))
        assert not client.cookies


@pytest.mark.parametrize("mode", ["unknown", "wrong", "disabled", "locked"])
def test_sign_in_failures_have_equivalent_http_response(mode: str) -> None:
    authentication = FakeAuthentication(sign_in_error=True)
    with TestClient(create_app(container=_container(authentication=authentication))) as client:
        response = client.post(
            "/auth/sign-in",
            headers={"Origin": ORIGIN},
            json={"username": mode, "password": "wrong"},
        )
    assert response.status_code == 401
    assert response.json() == {
        "error": {"code": "invalid_credentials", "message": "Invalid username or password."}
    }


def test_session_csrf_origin_request_id_and_control_actor() -> None:
    container = _container()
    request_id = str(uuid.uuid4())
    with TestClient(create_app(container=container)) as client:
        _sign_in(client)
        missing = client.post(
            "/api/identity-providers",
            headers={"Origin": ORIGIN},
            json={"slug": "acme", "issuer": "acme", "displayName": "Acme"},
        )
        assert missing.status_code == 403
        assert missing.json()["error"]["code"] == "admin_csrf_denied"

        wrong_origin = client.post(
            "/api/identity-providers",
            headers={"Origin": "http://evil.example", "X-CSRF-Token": "csrf-token"},
            json={"slug": "acme", "issuer": "acme", "displayName": "Acme"},
        )
        assert wrong_origin.status_code == 403

        created = client.post(
            "/api/identity-providers",
            headers={
                "Origin": ORIGIN,
                "X-CSRF-Token": "csrf-token",
                "X-Request-ID": request_id,
            },
            json={"slug": "acme", "issuer": "acme", "displayName": "Acme"},
        )
        assert created.status_code == 201
        assert created.headers["X-Request-ID"] == request_id
        assert container.identity_provider_control.actor.request_id == request_id
        assert {reason for _, reason, _ in container.authentication.denials} == {
            "csrf_mismatch",
            "origin_mismatch",
        }


def test_health_request_id_forwarded_ip_and_absent_operator_routes() -> None:
    container = _container()
    with TestClient(create_app(container=container)) as client:
        health = client.get("/health", headers={"X-Request-ID": "not/allowed"})
        assert health.status_code == 200
        uuid.UUID(health.headers["X-Request-ID"])
        assert client.post("/api/operators").status_code == 404
        assert client.post("/api/operator-management").status_code == 404

    request = Request(
        {
            "type": "http",
            "method": "GET",
            "path": "/",
            "headers": [(b"x-forwarded-for", b"203.0.113.8")],
            "client": ("127.0.0.1", 1234),
            "server": ("test", 80),
            "scheme": "http",
            "query_string": b"",
            "root_path": "",
        }
    )
    assert build_request_context(request, "127.0.0.1").ip_address == "203.0.113.8"
    assert build_request_context(request, "10.0.0.0/8").ip_address == "127.0.0.1"


def test_admin_ui_dist_validation_fails_fast(tmp_path: Path) -> None:
    missing = tmp_path / "missing"
    settings = AdminHttpSettings(_env_file=None, admin_ui_dist=missing)
    with pytest.raises(ValueError, match="does not exist"):
        create_app(container=_container(settings=settings))

    empty = tmp_path / "empty"
    empty.mkdir()
    settings = AdminHttpSettings(_env_file=None, admin_ui_dist=empty)
    with pytest.raises(ValueError, match="must contain index.html"):
        create_app(container=_container(settings=settings))


def test_admin_ui_spa_fallback_api_isolation_cache_and_security_headers(
    tmp_path: Path,
) -> None:
    dist = tmp_path / "dist"
    assets = dist / "assets"
    assets.mkdir(parents=True)
    (dist / "index.html").write_text("<html>admin-spa</html>", encoding="utf-8")
    (assets / "app-a1b2c3d4.js").write_text("console.log('admin')", encoding="utf-8")
    settings = AdminHttpSettings(_env_file=None, admin_ui_dist=dist)

    with TestClient(create_app(container=_container(settings=settings))) as client:
        for path in ("/", "/providers", f"/providers/{uuid.uuid4()}", "/audit"):
            response = client.get(path)
            assert response.status_code == 200
            assert "admin-spa" in response.text
            assert response.headers["Cache-Control"] == "no-store"
            assert response.headers["Content-Security-Policy"].startswith("default-src 'self'")
            assert response.headers["X-Frame-Options"] == "DENY"
            assert response.headers["X-Content-Type-Options"] == "nosniff"
            assert response.headers["Referrer-Policy"] == "no-referrer"
            assert "geolocation=()" in response.headers["Permissions-Policy"]

        asset = client.get("/assets/app-a1b2c3d4.js")
        assert asset.status_code == 200
        assert asset.headers["Cache-Control"] == "public, max-age=31536000, immutable"
        assert asset.headers["X-Frame-Options"] == "DENY"

        missing_asset = client.get("/assets/missing.js")
        assert missing_asset.status_code == 404
        assert "admin-spa" not in missing_asset.text

        missing_api = client.get("/api/not-a-real-route")
        assert missing_api.status_code == 404
        assert "admin-spa" not in missing_api.text

        missing_auth = client.get("/auth/not-a-real-route")
        assert missing_auth.status_code == 404
        assert "admin-spa" not in missing_auth.text
