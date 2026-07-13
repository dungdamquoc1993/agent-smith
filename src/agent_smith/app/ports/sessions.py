"""Application-level session lifecycle contracts."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Protocol

from agent_smith.core.agent.harness.session.session import Session
from agent_smith.core.agent.harness.session.types import SessionMetadata


@dataclass(frozen=True)
class PrincipalRecord:
    id: str
    display_name: str
    status: str
    created_at: datetime | None = None
    updated_at: datetime | None = None


@dataclass(frozen=True)
class SessionRecord:
    id: str
    principal_id: str
    title: str | None
    kind: str
    parent_session_id: str | None = None
    agent_name: str | None = None
    origin_task_id: str | None = None
    current_leaf_id: str | None = None
    provenance: dict[str, Any] = field(default_factory=dict)
    created_at: datetime | None = None
    updated_at: datetime | None = None


class PrincipalSessionDirectory(Protocol):
    """Principal lookup and session discovery used by App workflows."""

    async def ensure_principal(self, display_name: str) -> PrincipalRecord: ...

    async def list_sessions(
        self,
        *,
        principal_id: str,
        limit: int = 25,
    ) -> list[SessionRecord]: ...

    async def session_belongs_to(self, *, session_id: str, principal_id: str) -> bool: ...


class SessionCatalog(Protocol):
    """Lifecycle operations whose scope is wider than one harness session."""

    async def create(self, **options: Any) -> Session: ...

    async def open(self, metadata: SessionMetadata | dict[str, Any]) -> Session: ...

    async def fork(
        self,
        source: SessionMetadata | dict[str, Any],
        **options: Any,
    ) -> Session: ...
