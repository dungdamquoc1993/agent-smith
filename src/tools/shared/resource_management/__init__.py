"""Shared resource catalog CRUD helpers for manage_resources tool."""

from tools.shared.resource_management._handlers import (
    create_resource,
    delete_resource,
    list_resource_records,
    read_resource,
    update_resource,
)
from tools.shared.resource_management._validators import (
    build_create_content,
    merge_update_content,
    validate_content_for_kind,
)

__all__ = [
    "build_create_content",
    "create_resource",
    "delete_resource",
    "list_resource_records",
    "merge_update_content",
    "read_resource",
    "update_resource",
    "validate_content_for_kind",
]
