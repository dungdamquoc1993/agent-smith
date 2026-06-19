"""Postgres-backed resource catalog store."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlalchemy.orm import selectinload

from agent_smith.db.models.resource import (
    Resource as DbResource,
    ResourceKindEnum,
    ResourceScopeEnum,
    ResourceSourceTypeEnum,
    ResourceVersion as DbResourceVersion,
)
from agent_smith.resources.store import (
    ResourceConflictError,
    ResourceNotFoundError,
    ResourceStore,
    ResourceStoreError,
)
from agent_smith.resources.types import (
    ResourceCreate,
    ResourceKind,
    ResourceRecord,
    ResourceScope,
    ResourceUpdate,
    ResourceVersion,
    resource_content_hash,
)


class PostgresResourceStore(ResourceStore):
    """Postgres implementation of the generic resource catalog contract."""

    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        *,
        default_scope: ResourceScope = "user",
    ) -> None:
        self._session_factory = session_factory
        self.default_scope = default_scope

    async def list_resources(
        self,
        *,
        kind: ResourceKind | None = None,
        include_deleted: bool = False,
    ) -> list[ResourceRecord]:
        async with self._session_factory() as db:
            statement = (
                select(DbResource)
                .options(selectinload(DbResource.versions))
                .where(DbResource.scope == ResourceScopeEnum(self.default_scope))
                .order_by(DbResource.kind, DbResource.name)
            )
            if kind is not None:
                statement = statement.where(DbResource.kind == ResourceKindEnum(kind))
            if not include_deleted:
                statement = statement.where(DbResource.deleted_at.is_(None))
            rows = list((await db.scalars(statement)).unique())
            return [_row_to_record(row) for row in rows]

    async def get_resource(
        self,
        kind: ResourceKind,
        name: str,
        *,
        include_deleted: bool = False,
    ) -> ResourceRecord | None:
        async with self._session_factory() as db:
            row = await self._get_row(
                db,
                kind,
                name,
                include_deleted=include_deleted,
            )
            return _row_to_record(row) if row is not None else None

    async def create_resource(
        self,
        resource: ResourceCreate | dict[str, Any],
    ) -> ResourceRecord:
        resolved = self._resolve_create(resource)
        resource_id = uuid.uuid4()
        version_id = uuid.uuid4()
        version = DbResourceVersion(
            id=version_id,
            resource_id=resource_id,
            version=1,
            content=resolved.content,
            content_hash=resource_content_hash(resolved.content),
        )
        row = DbResource(
            id=resource_id,
            kind=ResourceKindEnum(resolved.kind),
            scope=ResourceScopeEnum(resolved.scope),
            name=resolved.name,
            source_type=ResourceSourceTypeEnum(resolved.source_type),
            description=resolved.description,
            source_uri=resolved.source_uri,
            disabled=resolved.disabled,
            versions=[version],
        )
        try:
            async with self._session_factory() as db, db.begin():
                db.add(row)
                await db.flush()
                await db.refresh(row, attribute_names=["versions"])
                return _row_to_record(row)
        except IntegrityError as exc:
            raise ResourceConflictError(
                f"Resource already exists: {resolved.kind}/{resolved.scope}/{resolved.name}"
            ) from exc

    async def update_resource(
        self,
        kind: ResourceKind,
        name: str,
        update: ResourceUpdate | dict[str, Any],
    ) -> ResourceRecord:
        resolved = (
            update
            if isinstance(update, ResourceUpdate)
            else ResourceUpdate.model_validate(update)
        )
        async with self._session_factory() as db, db.begin():
            row = await self._get_row_for_update(db, kind, name)
            if row is None:
                raise ResourceNotFoundError(f"Unknown resource: {kind}/{name}")

            if resolved.description is not None:
                row.description = resolved.description
            if resolved.source_uri is not None:
                row.source_uri = resolved.source_uri
            if resolved.disabled is not None:
                row.disabled = resolved.disabled
            if resolved.content is not None:
                next_version_number = _current_version(row).version + 1
                row.versions.append(
                    DbResourceVersion(
                        id=uuid.uuid4(),
                        resource_id=row.id,
                        version=next_version_number,
                        content=resolved.content,
                        content_hash=resource_content_hash(resolved.content),
                    )
                )

            await db.flush()
            await db.refresh(row, attribute_names=["versions"])
            return _row_to_record(row)

    async def delete_resource(self, kind: ResourceKind, name: str) -> None:
        async with self._session_factory() as db, db.begin():
            row = await self._get_row_for_update(db, kind, name)
            if row is None:
                raise ResourceNotFoundError(f"Unknown resource: {kind}/{name}")
            row.deleted_at = datetime.now(UTC)

    def _resolve_create(self, resource: ResourceCreate | dict[str, Any]) -> ResourceCreate:
        explicit_scope = False
        explicit_source_type = False
        if isinstance(resource, ResourceCreate):
            explicit_scope = "scope" in resource.model_fields_set
            explicit_source_type = (
                "source_type" in resource.model_fields_set
                or "sourceType" in resource.model_fields_set
            )
            data = resource.model_dump(mode="json", by_alias=True)
        else:
            explicit_scope = "scope" in resource
            explicit_source_type = "source_type" in resource or "sourceType" in resource
            data = dict(resource)

        if not explicit_scope:
            data["scope"] = self.default_scope
        if data.get("scope", self.default_scope) != self.default_scope:
            raise ResourceStoreError(
                "PostgresResourceStore only manages one scope per instance: "
                f"{self.default_scope}"
            )
        if not explicit_source_type:
            data["sourceType"] = "postgres"
        return ResourceCreate.model_validate(data)

    async def _get_row(
        self,
        db: AsyncSession,
        kind: ResourceKind,
        name: str,
        *,
        include_deleted: bool = False,
    ) -> DbResource | None:
        statement = (
            select(DbResource)
            .options(selectinload(DbResource.versions))
            .where(
                DbResource.kind == ResourceKindEnum(kind),
                DbResource.scope == ResourceScopeEnum(self.default_scope),
                DbResource.name == name,
            )
        )
        if not include_deleted:
            statement = statement.where(DbResource.deleted_at.is_(None))
        return (await db.scalars(statement)).unique().one_or_none()

    async def _get_row_for_update(
        self,
        db: AsyncSession,
        kind: ResourceKind,
        name: str,
    ) -> DbResource | None:
        statement = (
            select(DbResource)
            .options(selectinload(DbResource.versions))
            .where(
                DbResource.kind == ResourceKindEnum(kind),
                DbResource.scope == ResourceScopeEnum(self.default_scope),
                DbResource.name == name,
                DbResource.deleted_at.is_(None),
            )
            .with_for_update()
        )
        return (await db.scalars(statement)).unique().one_or_none()


def _row_to_record(row: DbResource) -> ResourceRecord:
    versions = [_version_to_record(version) for version in row.versions]
    current_version = max(versions, key=lambda version: version.version)
    return ResourceRecord(
        id=str(row.id),
        kind=_enum_value(row.kind),
        name=row.name,
        scope=_enum_value(row.scope),
        source_type=_enum_value(row.source_type),
        description=row.description,
        source_uri=row.source_uri,
        current_version=current_version,
        versions=versions,
        disabled=row.disabled,
        deleted_at=_datetime_to_iso(row.deleted_at),
        created_at=_required_datetime_to_iso(row.created_at),
        updated_at=_required_datetime_to_iso(row.updated_at),
    )


def _version_to_record(row: DbResourceVersion) -> ResourceVersion:
    return ResourceVersion(
        id=str(row.id),
        resource_id=str(row.resource_id),
        version=row.version,
        content=row.content,
        content_hash=row.content_hash,
        created_at=_required_datetime_to_iso(row.created_at),
    )


def _current_version(row: DbResource) -> DbResourceVersion:
    return max(row.versions, key=lambda version: version.version)


def _enum_value(value: Any) -> Any:
    return value.value if hasattr(value, "value") else value


def _datetime_to_iso(value: datetime | None) -> str | None:
    if value is None:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=UTC)
    return value.isoformat()


def _required_datetime_to_iso(value: datetime | None) -> str:
    return _datetime_to_iso(value) or datetime.now(UTC).isoformat()
