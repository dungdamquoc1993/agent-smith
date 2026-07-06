"""Session context helpers for permission rule visibility."""

from __future__ import annotations

from collections.abc import Awaitable, Callable

from agent_smith.core.agent.harness.session.session import Session
from agent_smith.core.agent.harness.session.types import SessionMetadata


async def visible_session_ids_for_rules(
    session: Session,
    *,
    lookup_metadata: Callable[[str], Awaitable[SessionMetadata | None]] | None = None,
) -> frozenset[str]:
    metadata = await session.get_metadata()
    visible: set[str] = {metadata.id}
    visited: set[str] = {metadata.id}

    parent_id = metadata.parent_session_id
    if parent_id is not None:
        visible.add(parent_id)

    if lookup_metadata is None:
        return frozenset(visible)

    current_id = parent_id
    while current_id is not None and current_id not in visited:
        visited.add(current_id)
        visible.add(current_id)
        parent_metadata = await lookup_metadata(current_id)
        if parent_metadata is None:
            break
        current_id = parent_metadata.parent_session_id

    return frozenset(visible)
