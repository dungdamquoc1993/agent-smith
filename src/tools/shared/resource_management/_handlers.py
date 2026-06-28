"""Resource catalog CRUD handlers."""

from __future__ import annotations

from ai.types import JsonObject
from resources import (
    ResourceCreate,
    ResourceNotFoundError,
    ResourceRecord,
    ResourceResolver,
    ResourceStore,
    ResourceUpdate,
)
from resources.types import ResourceKind
from tools.shared.common import text_result
from tools.shared.resource_management._details import kind_label, record_to_summary
from tools.shared.resource_management._validators import (
    build_create_content,
    merge_update_content,
)


async def list_resource_records(
    kind: ResourceKind,
    *,
    store: ResourceStore | None = None,
    resolver: ResourceResolver | None = None,
) -> list[ResourceRecord]:
    if resolver is not None:
        return await resolver.list_records(kind)
    if store is None:
        raise ValueError("store or resolver is required")
    return [
        record
        for record in await store.list_resources(kind=kind)
        if not record.disabled and not record.is_deleted
    ]


async def find_resource_record(
    kind: ResourceKind,
    name: str,
    *,
    store: ResourceStore | None = None,
    resolver: ResourceResolver | None = None,
) -> ResourceRecord | None:
    for record in await list_resource_records(kind, store=store, resolver=resolver):
        if record.name == name:
            return record
    return None


async def list_resources(
    store: ResourceStore,
    resolver: ResourceResolver | None,
    kind: ResourceKind,
):
    records = await list_resource_records(kind, store=store, resolver=resolver)
    resources = [record_to_summary(record, include_content=False) for record in records]
    names = ", ".join(str(item["name"]) for item in resources)
    label = kind_label(kind)
    return text_result(
        f"Found {len(resources)} {label}(s){f': {names}' if names else ''}.",
        details={"action": "list", "kind": kind, "resources": resources},
    )


async def read_resource(
    store: ResourceStore,
    resolver: ResourceResolver | None,
    kind: ResourceKind,
    name: str,
):
    record = await find_resource_record(kind, name, store=store, resolver=resolver)
    if record is None:
        raise ResourceNotFoundError(f"Unknown {kind_label(kind)}: {name}")
    details = record_to_summary(record, include_content=True)
    return text_result(
        f"Loaded {kind_label(kind)}: {name}.",
        details={"action": "read", "kind": kind, "resource": details},
    )


async def create_resource(
    store: ResourceStore,
    *,
    kind: ResourceKind,
    name: str,
    content: JsonObject,
    description: str | None = None,
    disabled: bool = False,
    source_uri: str | None = None,
):
    validated_content, resolved_description = build_create_content(
        kind,
        name=name,
        content=content,
        description=description,
    )
    record = await store.create_resource(
        ResourceCreate(
            kind=kind,
            name=name,
            description=resolved_description,
            source_uri=source_uri or (content.get("filePath") if kind == "skill" else None),
            disabled=disabled,
            content=validated_content,
        )
    )
    return text_result(
        f"Created {kind_label(kind)}: {name}.",
        details={
            "action": "create",
            "kind": kind,
            "resource": record_to_summary(record, include_content=True),
        },
    )


async def update_resource(
    store: ResourceStore,
    *,
    kind: ResourceKind,
    name: str,
    content: JsonObject | None = None,
    description: str | None = None,
    disabled: bool | None = None,
    source_uri: str | None = None,
):
    existing = await store.get_resource(kind, name)
    if existing is None:
        raise ResourceNotFoundError(f"Unknown {kind_label(kind)}: {name}")

    validated_content, updated_description = merge_update_content(
        kind,
        name=name,
        existing=existing,
        content_patch=content,
        description=description,
    )
    record = await store.update_resource(
        kind,
        name,
        ResourceUpdate(
            content=validated_content,
            description=updated_description,
            source_uri=source_uri,
            disabled=disabled,
        ),
    )
    return text_result(
        f"Updated {kind_label(kind)}: {name}.",
        details={
            "action": "update",
            "kind": kind,
            "resource": record_to_summary(record, include_content=True),
        },
    )


async def delete_resource(store: ResourceStore, kind: ResourceKind, name: str):
    await store.delete_resource(kind, name)
    return text_result(
        f"Deleted {kind_label(kind)}: {name}.",
        details={"action": "delete", "kind": kind, "name": name},
    )
