"""Read-only filesystem resource store."""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from agent_smith.resources.store import ResourceReadOnlyError, ResourceStore, ResourceStoreError
from agent_smith.resources.types import (
    ResourceCreate,
    ResourceKind,
    ResourceRecord,
    ResourceScope,
    ResourceSourceType,
    ResourceUpdate,
    ResourceVersion,
    resource_content_hash,
)


class FilesystemResourceStore(ResourceStore):
    def __init__(
        self,
        root: str | Path,
        *,
        scope: ResourceScope = "file",
        source_type: ResourceSourceType = "filesystem",
    ) -> None:
        self.root = Path(root)
        self.scope = scope
        self.source_type = source_type

    async def list_resources(
        self,
        *,
        kind: ResourceKind | None = None,
        include_deleted: bool = False,
    ) -> list[ResourceRecord]:
        _ = include_deleted
        records = self._load_resources()
        if kind is not None:
            records = [record for record in records if record.kind == kind]
        records.sort(key=lambda record: (record.kind, record.name, record.source_uri or ""))
        return [record.model_copy(deep=True) for record in records]

    async def get_resource(
        self,
        kind: ResourceKind,
        name: str,
        *,
        include_deleted: bool = False,
    ) -> ResourceRecord | None:
        _ = include_deleted
        for record in await self.list_resources(kind=kind):
            if record.name == name:
                return record
        return None

    async def create_resource(self, resource: ResourceCreate | dict[str, Any]) -> ResourceRecord:
        _ = resource
        raise ResourceReadOnlyError("FilesystemResourceStore is read-only")

    async def update_resource(
        self,
        kind: ResourceKind,
        name: str,
        update: ResourceUpdate | dict[str, Any],
    ) -> ResourceRecord:
        _ = kind, name, update
        raise ResourceReadOnlyError("FilesystemResourceStore is read-only")

    async def delete_resource(self, kind: ResourceKind, name: str) -> None:
        _ = kind, name
        raise ResourceReadOnlyError("FilesystemResourceStore is read-only")

    def _load_resources(self) -> list[ResourceRecord]:
        if not self.root.exists():
            return []
        records: dict[tuple[ResourceKind, str], ResourceRecord] = {}
        for path in sorted(self.root.rglob("*")):
            if not path.is_file():
                continue
            record = self._record_for_path(path)
            if record:
                records[(record.kind, record.name)] = record
        return list[ResourceRecord](records.values())

    def _record_for_path(self, path: Path) -> ResourceRecord | None:
        if self._is_skill_path(path):
            metadata, body = _read_markdown(path)
            name = str(metadata.get("name") or _skill_name_from_path(path))
            description = str(metadata.get("description") or _first_heading(body) or name)
            content = {
                "name": name,
                "description": description,
                "content": body.strip(),
                "filePath": str(path),
            }
            if "disableModelInvocation" in metadata:
                content["disableModelInvocation"] = metadata["disableModelInvocation"]
            elif "disable_model_invocation" in metadata:
                content["disableModelInvocation"] = metadata["disable_model_invocation"]
            return self._make_record(path, "skill", name, description, content)

        if self._is_prompt_template_path(path):
            metadata, body = _read_markdown(path)
            name = str(metadata.get("name") or _strip_suffixes(path.name, [".prompt.md", ".md"]))
            description = metadata.get("description")
            content = {
                "name": name,
                "description": str(description) if description is not None else None,
                "content": body.strip(),
            }
            return self._make_record(path, "prompt_template", name, content["description"], content)

        if self._is_agent_definition_path(path):
            data = self._read_json_object(path)
            name = str(data.get("name") or _strip_suffixes(path.name, [".agent.json", ".json"]))
            data.setdefault("name", name)
            description = str(data.get("description") or data.get("whenToUse") or name)
            data.setdefault("description", description)
            return self._make_record(path, "agent_definition", name, description, data)

        if self._is_mcp_config_path(path):
            data = self._read_json_object(path)
            name = str(data.get("name") or _strip_suffixes(path.name, [".mcp.json", ".json"]))
            data.setdefault("name", name)
            description = data.get("description")
            return self._make_record(
                path,
                "mcp_server_config",
                name,
                str(description) if description is not None else None,
                data,
            )

        return None

    def _make_record(
        self,
        path: Path,
        kind: ResourceKind,
        name: str,
        description: str | None,
        content: dict[str, Any],
    ) -> ResourceRecord:
        resolved = path.resolve()
        resource_id = f"fs:{hashlib.sha256(str(resolved).encode('utf-8')).hexdigest()}"
        mtime = datetime.fromtimestamp(path.stat().st_mtime, UTC).isoformat()
        version = ResourceVersion(
            id=f"{resource_id}:1",
            resource_id=resource_id,
            version=1,
            content=content,
            content_hash=resource_content_hash(content),
            created_at=mtime,
        )
        return ResourceRecord(
            id=resource_id,
            kind=kind,
            name=name,
            scope=self.scope,
            source_type=self.source_type,
            description=description,
            source_uri=str(resolved),
            current_version=version,
            versions=[version],
            created_at=mtime,
            updated_at=mtime,
        )

    def _read_json_object(self, path: Path) -> dict[str, Any]:
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise ResourceStoreError(f"Invalid JSON resource {path}: {exc}") from exc
        if not isinstance(data, dict):
            raise ResourceStoreError(f"JSON resource must be an object: {path}")
        return data

    def _is_skill_path(self, path: Path) -> bool:
        return path.name == "SKILL.md" or path.name.endswith(".skill.md")

    def _is_prompt_template_path(self, path: Path) -> bool:
        return (
            path.name.endswith(".prompt.md")
            or (path.suffix == ".md" and path.parent.name in {"prompts", "prompt_templates"})
        )

    def _is_agent_definition_path(self, path: Path) -> bool:
        return (
            path.name.endswith(".agent.json")
            or (path.suffix == ".json" and path.parent.name == "agents")
        )

    def _is_mcp_config_path(self, path: Path) -> bool:
        return (
            path.name.endswith(".mcp.json")
            or (path.suffix == ".json" and path.parent.name in {"mcp", "mcp_servers"})
        )


def _read_markdown(path: Path) -> tuple[dict[str, Any], str]:
    text = path.read_text(encoding="utf-8")
    if not text.startswith("---\n"):
        return {}, text
    end = text.find("\n---", 4)
    if end == -1:
        return {}, text
    header = text[4:end]
    body_start = end + len("\n---")
    if body_start < len(text) and text[body_start] == "\n":
        body_start += 1
    return _parse_frontmatter(header), text[body_start:]


def _parse_frontmatter(header: str) -> dict[str, Any]:
    metadata: dict[str, Any] = {}
    for line in header.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or ":" not in stripped:
            continue
        key, value = stripped.split(":", 1)
        metadata[key.strip()] = _parse_scalar(value.strip())
    return metadata


def _parse_scalar(value: str) -> Any:
    if value.lower() == "true":
        return True
    if value.lower() == "false":
        return False
    if (
        len(value) >= 2
        and value[0] == value[-1]
        and value[0] in {"'", '"'}
    ):
        return value[1:-1]
    if value.startswith("[") and value.endswith("]"):
        return [part.strip().strip("'\"") for part in value[1:-1].split(",") if part.strip()]
    return value


def _first_heading(body: str) -> str | None:
    for line in body.splitlines():
        stripped = line.strip()
        if stripped.startswith("#"):
            return stripped.lstrip("#").strip() or None
        if stripped:
            return stripped[:120]
    return None


def _skill_name_from_path(path: Path) -> str:
    if path.name == "SKILL.md":
        return path.parent.name
    return _strip_suffixes(path.name, [".skill.md", ".md"])


def _strip_suffixes(name: str, suffixes: list[str]) -> str:
    for suffix in suffixes:
        if name.endswith(suffix):
            return name[: -len(suffix)]
    return name
