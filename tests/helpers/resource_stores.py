"""Test doubles for resource store behavior."""

from __future__ import annotations

from typing import Any

from agent_smith.core.resources.store import ResourceReadOnlyError, ResourceStore
from agent_smith.core.resources.types import ResourceCreate, ResourceKind, ResourceRecord, ResourceUpdate


class ReadOnlyResourceStore:
    """Test double for stores that reject mutations."""

    def __init__(self, inner: ResourceStore) -> None:
        self._inner = inner

    async def list_resources(
        self,
        *,
        kind: ResourceKind | None = None,
        include_deleted: bool = False,
    ) -> list[ResourceRecord]:
        return await self._inner.list_resources(kind=kind, include_deleted=include_deleted)

    async def get_resource(
        self,
        kind: ResourceKind,
        name: str,
        *,
        include_deleted: bool = False,
    ) -> ResourceRecord | None:
        return await self._inner.get_resource(kind, name, include_deleted=include_deleted)

    async def create_resource(self, resource: ResourceCreate | dict[str, Any]) -> ResourceRecord:
        _ = resource
        raise ResourceReadOnlyError("Read-only resource store")

    async def update_resource(
        self,
        kind: ResourceKind,
        name: str,
        update: ResourceUpdate | dict[str, Any],
    ) -> ResourceRecord:
        _ = kind, name, update
        raise ResourceReadOnlyError("Read-only resource store")

    async def delete_resource(self, kind: ResourceKind, name: str) -> None:
        _ = kind, name
        raise ResourceReadOnlyError("Read-only resource store")
