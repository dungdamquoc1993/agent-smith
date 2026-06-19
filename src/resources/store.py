"""Resource store contracts."""

from __future__ import annotations

from typing import Any, Protocol, runtime_checkable

from resources.types import (
    ResourceCreate,
    ResourceKind,
    ResourceRecord,
    ResourceUpdate,
)


class ResourceStoreError(Exception):
    pass


class ResourceConflictError(ResourceStoreError):
    pass


class ResourceNotFoundError(ResourceStoreError):
    pass


class ResourceReadOnlyError(ResourceStoreError):
    pass


@runtime_checkable
class ResourceStore(Protocol):
    async def list_resources(
        self,
        *,
        kind: ResourceKind | None = None,
        include_deleted: bool = False,
    ) -> list[ResourceRecord]: ...

    async def get_resource(
        self,
        kind: ResourceKind,
        name: str,
        *,
        include_deleted: bool = False,
    ) -> ResourceRecord | None: ...

    async def create_resource(
        self,
        resource: ResourceCreate | dict[str, Any],
    ) -> ResourceRecord: ...

    async def update_resource(
        self,
        kind: ResourceKind,
        name: str,
        update: ResourceUpdate | dict[str, Any],
    ) -> ResourceRecord: ...

    async def delete_resource(self, kind: ResourceKind, name: str) -> None: ...
