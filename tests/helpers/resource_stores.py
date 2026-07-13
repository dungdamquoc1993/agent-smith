"""Test doubles for resource store behavior."""

from __future__ import annotations

import uuid
from typing import Any

from agent_smith.core.resources.store import (
    ResourceConflictError,
    ResourceNotFoundError,
    ResourceReadOnlyError,
    ResourceStore,
)
from agent_smith.core.resources.types import (
    ResourceCreate,
    ResourceKind,
    ResourceRecord,
    ResourceUpdate,
    ResourceVersion,
    resource_content_hash,
    utc_now_iso,
)


class MemoryResourceStore(ResourceStore):
    """In-memory resource catalog used only as a test double."""

    def __init__(self, resources: list[ResourceCreate | dict[str, Any]] | None = None) -> None:
        self._records: dict[tuple[ResourceKind, str], ResourceRecord] = {}
        if resources:
            for resource in resources:
                self.add_initial_resource(resource)

    def add_initial_resource(self, resource: ResourceCreate | dict[str, Any]) -> ResourceRecord:
        resolved = self._resolve_create(resource)
        record = self._make_record(resolved)
        self._records[(record.kind, record.name)] = record
        return record.model_copy(deep=True)

    async def list_resources(
        self,
        *,
        kind: ResourceKind | None = None,
        include_deleted: bool = False,
    ) -> list[ResourceRecord]:
        records = [
            record
            for record in self._records.values()
            if (kind is None or record.kind == kind) and (include_deleted or not record.is_deleted)
        ]
        records.sort(key=lambda record: (record.kind, record.name))
        return [record.model_copy(deep=True) for record in records]

    async def get_resource(
        self,
        kind: ResourceKind,
        name: str,
        *,
        include_deleted: bool = False,
    ) -> ResourceRecord | None:
        record = self._records.get((kind, name))
        if record is None or (record.is_deleted and not include_deleted):
            return None
        return record.model_copy(deep=True)

    async def create_resource(
        self,
        resource: ResourceCreate | dict[str, Any],
    ) -> ResourceRecord:
        resolved = self._resolve_create(resource)
        key = (resolved.kind, resolved.name)
        existing = self._records.get(key)
        if existing and not existing.is_deleted:
            raise ResourceConflictError(f"Resource already exists: {resolved.kind}/{resolved.name}")
        record = self._make_record(resolved)
        self._records[key] = record
        return record.model_copy(deep=True)

    async def update_resource(
        self,
        kind: ResourceKind,
        name: str,
        update: ResourceUpdate | dict[str, Any],
    ) -> ResourceRecord:
        record = self._records.get((kind, name))
        if record is None or record.is_deleted:
            raise ResourceNotFoundError(f"Unknown resource: {kind}/{name}")
        resolved = (
            update if isinstance(update, ResourceUpdate) else ResourceUpdate.model_validate(update)
        )

        next_record = record.model_copy(deep=True)
        if resolved.description is not None:
            next_record.description = resolved.description
        if resolved.source_uri is not None:
            next_record.source_uri = resolved.source_uri
        if resolved.disabled is not None:
            next_record.disabled = resolved.disabled
        if resolved.content is not None:
            next_version = ResourceVersion(
                id=str(uuid.uuid4()),
                resource_id=next_record.id,
                version=next_record.current_version.version + 1,
                content=resolved.content,
                content_hash=resource_content_hash(resolved.content),
            )
            next_record.versions.append(next_version)
            next_record.current_version = next_version
        next_record.updated_at = utc_now_iso()
        self._records[(kind, name)] = next_record
        return next_record.model_copy(deep=True)

    async def delete_resource(self, kind: ResourceKind, name: str) -> None:
        record = self._records.get((kind, name))
        if record is None or record.is_deleted:
            raise ResourceNotFoundError(f"Unknown resource: {kind}/{name}")
        next_record = record.model_copy(deep=True)
        now = utc_now_iso()
        next_record.deleted_at = now
        next_record.updated_at = now
        self._records[(kind, name)] = next_record

    @staticmethod
    def _resolve_create(resource: ResourceCreate | dict[str, Any]) -> ResourceCreate:
        return (
            resource
            if isinstance(resource, ResourceCreate)
            else ResourceCreate.model_validate(resource)
        )

    @staticmethod
    def _make_record(resource: ResourceCreate) -> ResourceRecord:
        resource_id = str(uuid.uuid4())
        version = ResourceVersion(
            id=str(uuid.uuid4()),
            resource_id=resource_id,
            version=1,
            content=resource.content,
            content_hash=resource_content_hash(resource.content),
        )
        now = utc_now_iso()
        return ResourceRecord(
            id=resource_id,
            kind=resource.kind,
            name=resource.name,
            scope=resource.scope,
            source_type=resource.source_type,
            description=resource.description,
            source_uri=resource.source_uri,
            current_version=version,
            versions=[version],
            disabled=resource.disabled,
            created_at=now,
            updated_at=now,
        )


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
