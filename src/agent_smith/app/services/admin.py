"""Admin operator and password/session authentication use cases."""

from __future__ import annotations

import hashlib
import hmac
import re
from datetime import datetime, timedelta
from typing import Any

from agent_smith.app.ports.admin import (
    AdminActorContext,
    AdminAuditOutcome,
    AdminAuditEvent,
    AdminAuthenticationSessions,
    AuthenticatedAdminSession,
    AdminOperatorAdministration,
    AdminOperatorRecord,
    AdminSessionRecord,
    Clock,
    CreatedAdminSession,
    PasswordHasher,
    SecureTokenGenerator,
)

MIN_PASSWORD_LENGTH = 8
LOGIN_FAILURE_LIMIT = 5
LOGIN_LOCK_DURATION = timedelta(minutes=15)
SESSION_IDLE_TTL = timedelta(hours=24)
SESSION_ABSOLUTE_TTL = timedelta(days=7)
SIGN_IN_THROTTLE_LIMIT = 10
SIGN_IN_THROTTLE_WINDOW = timedelta(minutes=15)
_USERNAME_PATTERN = re.compile(r"^[a-z0-9][a-z0-9._@-]{0,127}$")
_SENSITIVE_AUDIT_FRAGMENTS = (
    "authorization",
    "cookie",
    "credential",
    "hash",
    "password",
    "secret",
    "token",
)


class AdminValidationError(ValueError):
    """An admin management input is invalid."""


class AdminOperatorNotFoundError(Exception):
    """The requested operator does not exist."""


class AdminAuthenticationError(Exception):
    """Admin credentials or session are invalid."""


def normalize_admin_username(username: str) -> str:
    normalized = username.strip().lower()
    if not _USERNAME_PATTERN.fullmatch(normalized):
        raise AdminValidationError(
            "Username must be 1-128 characters using letters, numbers, '.', '_', '@', or '-'."
        )
    return normalized


def validate_admin_password(password: str) -> None:
    if len(password) < MIN_PASSWORD_LENGTH:
        raise AdminValidationError(
            f"Password must contain at least {MIN_PASSWORD_LENGTH} characters."
        )


def sanitize_audit_metadata(metadata: dict[str, Any]) -> dict[str, Any]:
    """Recursively omit secret-bearing fields before audit persistence."""

    sanitized: dict[str, Any] = {}
    for key, value in metadata.items():
        lowered = key.lower().replace("-", "_")
        if any(fragment in lowered for fragment in _SENSITIVE_AUDIT_FRAGMENTS):
            continue
        if isinstance(value, dict):
            sanitized[key] = sanitize_audit_metadata(value)
        elif isinstance(value, list):
            sanitized[key] = [
                sanitize_audit_metadata(item) if isinstance(item, dict) else item
                for item in value
            ]
        else:
            sanitized[key] = value
    return sanitized


def hash_admin_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


class AdminOperatorService:
    def __init__(
        self,
        operators: AdminOperatorAdministration,
        password_hasher: PasswordHasher,
        clock: Clock,
    ) -> None:
        self._operators = operators
        self._password_hasher = password_hasher
        self._clock = clock

    async def bootstrap_admin(
        self,
        *,
        username: str,
        display_name: str,
        password: str,
        actor: AdminActorContext,
    ) -> AdminOperatorRecord:
        normalized, name = self._validate_operator(username, display_name, password)
        now = self._clock.now()
        return await self._operators.bootstrap_operator(
            username=normalized,
            display_name=name,
            password_hash=self._password_hasher.hash(password),
            now=now,
            audit=self._audit(
                actor,
                "admin.operator.bootstrap",
                normalized,
                now,
            ),
        )

    async def add_admin(
        self,
        *,
        username: str,
        display_name: str,
        password: str,
        actor: AdminActorContext,
    ) -> AdminOperatorRecord:
        normalized, name = self._validate_operator(username, display_name, password)
        now = self._clock.now()
        return await self._operators.add_operator(
            username=normalized,
            display_name=name,
            password_hash=self._password_hasher.hash(password),
            now=now,
            audit=self._audit(actor, "admin.operator.create", normalized, now),
        )

    async def reset_password(
        self, *, username: str, password: str, actor: AdminActorContext
    ) -> AdminOperatorRecord:
        normalized = normalize_admin_username(username)
        validate_admin_password(password)
        now = self._clock.now()
        operator = await self._operators.reset_password(
            username=normalized,
            password_hash=self._password_hasher.hash(password),
            now=now,
            audit=self._audit(actor, "admin.operator.password_reset", normalized, now),
        )
        if operator is None:
            raise AdminOperatorNotFoundError("Admin operator was not found.")
        return operator

    async def disable_admin(
        self, *, username: str, actor: AdminActorContext
    ) -> AdminOperatorRecord:
        normalized = normalize_admin_username(username)
        now = self._clock.now()
        operator = await self._operators.disable_operator(
            username=normalized,
            now=now,
            audit=self._audit(actor, "admin.operator.disable", normalized, now),
        )
        if operator is None:
            raise AdminOperatorNotFoundError("Admin operator was not found.")
        return operator

    async def count_admins(self) -> int:
        return await self._operators.count_operators()

    async def get_admin(self, username: str) -> AdminOperatorRecord | None:
        return await self._operators.get_operator(normalize_admin_username(username))

    @staticmethod
    def _validate_operator(username: str, display_name: str, password: str) -> tuple[str, str]:
        normalized = normalize_admin_username(username)
        name = display_name.strip()
        if not name or len(name) > 255:
            raise AdminValidationError("Display name must contain 1-255 characters.")
        validate_admin_password(password)
        return normalized, name

    @staticmethod
    def _audit(
        actor: AdminActorContext, action: str, username: str, occurred_at: datetime
    ) -> AdminAuditEvent:
        return AdminAuditEvent(
            actor=actor,
            action=action,
            outcome="success",
            resource_type="admin_operator",
            resource_id=username,
            occurred_at=occurred_at,
        )


class AdminAuthenticationService:
    def __init__(
        self,
        sessions: AdminAuthenticationSessions,
        password_hasher: PasswordHasher,
        token_generator: SecureTokenGenerator,
        clock: Clock,
        *,
        dummy_password_hash: str | None = None,
    ) -> None:
        self._sessions = sessions
        self._password_hasher = password_hasher
        self._token_generator = token_generator
        self._clock = clock
        self._dummy_password_hash = dummy_password_hash or password_hasher.hash(
            "admin-dummy-verification-value"
        )

    async def sign_in(
        self,
        *,
        username: str,
        password: str,
        ip_address: str | None = None,
        user_agent: str | None = None,
        request_id: str | None = None,
    ) -> CreatedAdminSession:
        try:
            normalized = normalize_admin_username(username)
        except AdminValidationError:
            normalized = "invalid"
        now = self._clock.now()
        username_denials, ip_denials = await self._sessions.count_denied_sign_ins(
            username=normalized,
            ip_address=ip_address,
            since=now - SIGN_IN_THROTTLE_WINDOW,
        )
        if username_denials >= SIGN_IN_THROTTLE_LIMIT or ip_denials >= SIGN_IN_THROTTLE_LIMIT:
            self._password_hasher.verify(self._dummy_password_hash, password)
            await self._sessions.append_audit(
                self._login_audit(
                    now=now,
                    outcome="denied",
                    username=normalized,
                    operator_id=None,
                    ip_address=ip_address,
                    user_agent=user_agent,
                    request_id=request_id,
                    reason="throttled",
                )
            )
            raise AdminAuthenticationError("Invalid username or password.")
        operator = await self._sessions.find_operator(normalized)
        if operator is None:
            self._password_hasher.verify(self._dummy_password_hash, password)
            await self._sessions.append_audit(
                self._login_audit(
                    now=now,
                    outcome="denied",
                    username=normalized,
                    operator_id=None,
                    ip_address=ip_address,
                    user_agent=user_agent,
                    request_id=request_id,
                    reason="invalid_credentials",
                )
            )
            raise AdminAuthenticationError("Invalid username or password.")

        password_valid = self._password_hasher.verify(operator.password_hash, password)
        locked = operator.locked_until is not None and operator.locked_until > now
        if operator.status != "active" or locked or not password_valid:
            if operator.status == "active" and not locked and not password_valid:
                operator = await self._sessions.record_login_failure(
                    operator_id=operator.id,
                    now=now,
                    failure_limit=LOGIN_FAILURE_LIMIT,
                    lock_until=now + LOGIN_LOCK_DURATION,
                    audit=self._login_audit(
                        now=now,
                        outcome="denied",
                        username=normalized,
                        operator_id=operator.id,
                        ip_address=ip_address,
                        user_agent=user_agent,
                        request_id=request_id,
                        reason="invalid_credentials",
                    ),
                )
            else:
                reason = "disabled" if operator.status != "active" else "locked"
                await self._sessions.append_audit(
                    self._login_audit(
                        now=now,
                        outcome="denied",
                        username=normalized,
                        operator_id=operator.id,
                        ip_address=ip_address,
                        user_agent=user_agent,
                        request_id=request_id,
                        reason=reason,
                    )
                )
            raise AdminAuthenticationError("Invalid username or password.")

        session_token = self._token_generator.generate()
        csrf_token = self._token_generator.generate()
        absolute_expires_at = now + SESSION_ABSOLUTE_TTL
        replacement_hash = (
            self._password_hasher.hash(password)
            if self._password_hasher.needs_rehash(operator.password_hash)
            else None
        )
        operator, session = await self._sessions.create_session_after_login(
            operator_id=operator.id,
            token_hash=hash_admin_token(session_token),
            csrf_token_hash=hash_admin_token(csrf_token),
            now=now,
            idle_expires_at=now + SESSION_IDLE_TTL,
            absolute_expires_at=absolute_expires_at,
            ip_address=ip_address,
            user_agent=user_agent,
            replacement_password_hash=replacement_hash,
            audit=self._login_audit(
                now=now,
                outcome="success",
                username=normalized,
                operator_id=operator.id,
                ip_address=ip_address,
                user_agent=user_agent,
                request_id=request_id,
            ),
        )
        return CreatedAdminSession(
            operator=operator,
            session=session,
            session_token=session_token,
            csrf_token=csrf_token,
        )

    async def verify_session(
        self, session_token: str, *, touch: bool = True
    ) -> AuthenticatedAdminSession:
        now = self._clock.now()
        resolved = await self._sessions.resolve_session(hash_admin_token(session_token))
        if resolved is None:
            raise AdminAuthenticationError("Invalid admin session.")
        operator, session = resolved
        if (
            operator.status != "active"
            or session.revoked_at is not None
            or session.idle_expires_at <= now
            or session.absolute_expires_at <= now
        ):
            raise AdminAuthenticationError("Invalid admin session.")
        if touch:
            idle_expires_at = min(now + SESSION_IDLE_TTL, session.absolute_expires_at)
            touched = await self._sessions.touch_session(
                session_id=session.id,
                now=now,
                idle_expires_at=idle_expires_at,
            )
            if touched is None:
                raise AdminAuthenticationError("Invalid admin session.")
            session = touched
        actor = AdminActorContext(
            kind="admin_operator", identifier=operator.username,
            operator_id=operator.id, session_id=session.id,
            ip_address=session.ip_address, user_agent=session.user_agent,
        )
        return AuthenticatedAdminSession(operator=operator, session=session, actor=actor)

    @staticmethod
    def verify_csrf(session: AdminSessionRecord, csrf_cookie: str, csrf_header: str) -> bool:
        return bool(
            csrf_cookie
            and csrf_header
            and hmac.compare_digest(csrf_cookie, csrf_header)
            and hmac.compare_digest(hash_admin_token(csrf_header), session.csrf_token_hash)
        )

    async def audit_denial(
        self,
        *,
        action: str,
        reason: str,
        actor: AdminActorContext,
    ) -> None:
        await self._sessions.append_audit(
            AdminAuditEvent(
                actor=actor,
                action=action,
                outcome="denied",
                metadata={"reason": reason},
                occurred_at=self._clock.now(),
            )
        )

    async def sign_out(
        self,
        session_token: str,
        *,
        request_id: str | None = None,
    ) -> None:
        now = self._clock.now()
        resolved = await self._sessions.resolve_session(hash_admin_token(session_token))
        if resolved is None:
            return
        operator, session = resolved
        actor = AdminActorContext(
            kind="admin_operator",
            identifier=operator.username,
            operator_id=operator.id,
            session_id=session.id,
            request_id=request_id,
            ip_address=session.ip_address,
            user_agent=session.user_agent,
        )
        await self._sessions.revoke_session(
            session_id=session.id,
            now=now,
            audit=AdminAuditEvent(
                actor=actor,
                action="admin.auth.sign_out",
                outcome="success",
                resource_type="admin_session",
                resource_id=session.id,
                occurred_at=now,
            ),
        )

    @staticmethod
    def _login_audit(
        *,
        now: datetime,
        outcome: AdminAuditOutcome,
        username: str,
        operator_id: str | None,
        ip_address: str | None,
        user_agent: str | None,
        request_id: str | None,
        reason: str | None = None,
    ) -> AdminAuditEvent:
        authenticated = outcome == "success" and operator_id is not None
        return AdminAuditEvent(
            actor=AdminActorContext(
                kind="admin_operator" if authenticated else "anonymous",
                identifier=username,
                operator_id=operator_id if authenticated else None,
                request_id=request_id,
                ip_address=ip_address,
                user_agent=user_agent,
            ),
            action="admin.auth.sign_in",
            outcome=outcome,
            resource_type="admin_operator",
            resource_id=username,
            metadata={"reason": reason} if reason else {},
            occurred_at=now,
        )
