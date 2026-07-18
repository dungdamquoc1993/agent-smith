"""Infrastructure-independent contracts for admin identity and sessions."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Literal, Protocol

AdminOperatorStatus = Literal["active", "disabled"]
AdminAuditOutcome = Literal["success", "denied", "failed"]


class AdminStoreConflictError(Exception):
    """A unique admin identity already exists."""


class AdminBootstrapConflictError(Exception):
    """The first administrator has already been bootstrapped."""


class AdminBootstrapRequiredError(Exception):
    """The first administrator must be created with the bootstrap workflow."""


class LastActiveAdminError(Exception):
    """The requested change would leave no active administrator."""


@dataclass(frozen=True)
class AdminOperatorRecord:
    id: str
    username: str
    display_name: str
    password_hash: str
    status: AdminOperatorStatus
    failed_login_count: int = 0
    locked_until: datetime | None = None
    last_login_at: datetime | None = None
    password_changed_at: datetime | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None


@dataclass(frozen=True)
class AdminSessionRecord:
    id: str
    operator_id: str
    token_hash: str
    csrf_token_hash: str
    created_at: datetime
    last_seen_at: datetime
    idle_expires_at: datetime
    absolute_expires_at: datetime
    revoked_at: datetime | None = None
    ip_address: str | None = None
    user_agent: str | None = None


@dataclass(frozen=True)
class AdminActorContext:
    kind: str
    identifier: str | None = None
    operator_id: str | None = None
    session_id: str | None = None
    request_id: str | None = None
    ip_address: str | None = None
    user_agent: str | None = None


@dataclass(frozen=True)
class AdminAuditEvent:
    actor: AdminActorContext
    action: str
    outcome: AdminAuditOutcome
    resource_type: str | None = None
    resource_id: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)
    occurred_at: datetime | None = None
    id: str | None = None


@dataclass(frozen=True)
class CreatedAdminSession:
    operator: AdminOperatorRecord
    session: AdminSessionRecord
    session_token: str
    csrf_token: str


@dataclass(frozen=True)
class AuthenticatedAdminSession:
    operator: AdminOperatorRecord
    session: AdminSessionRecord
    actor: AdminActorContext


class PasswordHasher(Protocol):
    def hash(self, password: str) -> str: ...

    def verify(self, password_hash: str, password: str) -> bool: ...

    def needs_rehash(self, password_hash: str) -> bool: ...


class SecureTokenGenerator(Protocol):
    def generate(self) -> str: ...


class Clock(Protocol):
    def now(self) -> datetime: ...


class AdminOperatorAdministration(Protocol):
    async def count_operators(self) -> int: ...

    async def get_operator(self, username: str) -> AdminOperatorRecord | None: ...

    async def bootstrap_operator(
        self,
        *,
        username: str,
        display_name: str,
        password_hash: str,
        now: datetime,
        audit: AdminAuditEvent,
    ) -> AdminOperatorRecord: ...

    async def add_operator(
        self,
        *,
        username: str,
        display_name: str,
        password_hash: str,
        now: datetime,
        audit: AdminAuditEvent,
    ) -> AdminOperatorRecord: ...

    async def reset_password(
        self,
        *,
        username: str,
        password_hash: str,
        now: datetime,
        audit: AdminAuditEvent,
    ) -> AdminOperatorRecord | None: ...

    async def disable_operator(
        self,
        *,
        username: str,
        now: datetime,
        audit: AdminAuditEvent,
    ) -> AdminOperatorRecord | None: ...


class AdminAuthenticationSessions(Protocol):
    async def find_operator(self, username: str) -> AdminOperatorRecord | None: ...

    async def record_login_failure(
        self,
        *,
        operator_id: str,
        now: datetime,
        failure_limit: int,
        lock_until: datetime,
        audit: AdminAuditEvent,
    ) -> AdminOperatorRecord: ...

    async def append_audit(self, event: AdminAuditEvent) -> None: ...

    async def count_denied_sign_ins(
        self,
        *,
        username: str,
        ip_address: str | None,
        since: datetime,
    ) -> tuple[int, int]: ...

    async def create_session_after_login(
        self,
        *,
        operator_id: str,
        token_hash: str,
        csrf_token_hash: str,
        now: datetime,
        idle_expires_at: datetime,
        absolute_expires_at: datetime,
        ip_address: str | None,
        user_agent: str | None,
        replacement_password_hash: str | None,
        audit: AdminAuditEvent,
    ) -> tuple[AdminOperatorRecord, AdminSessionRecord]: ...

    async def resolve_session(
        self, token_hash: str
    ) -> tuple[AdminOperatorRecord, AdminSessionRecord] | None: ...

    async def touch_session(
        self, *, session_id: str, now: datetime, idle_expires_at: datetime
    ) -> AdminSessionRecord | None: ...

    async def revoke_session(
        self, *, session_id: str, now: datetime, audit: AdminAuditEvent
    ) -> bool: ...


class AdminAuditReader(Protocol):
    async def list_audit_events(
        self,
        *,
        limit: int = 50,
        before: datetime | None = None,
        before_id: str | None = None,
        action: str | None = None,
        outcome: AdminAuditOutcome | None = None,
        actor_operator_id: str | None = None,
        resource_type: str | None = None,
        resource_id: str | None = None,
    ) -> list[AdminAuditEvent]: ...
