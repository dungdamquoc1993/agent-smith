"""Resource catalog use cases."""

from __future__ import annotations

from typing import Any

from agent_smith.core.resources import ResourceConflictError, ResourceStore


class ResourceService:
    def __init__(
        self,
        store: ResourceStore,
        *,
        default_agent_name: str,
    ) -> None:
        self._store = store
        self.default_agent_name = default_agent_name

    async def list_resources(self) -> dict[str, Any]:
        records = await self.store().list_resources()
        return {
            "resources": [
                record.model_dump(mode="json", by_alias=True, exclude_none=True)
                for record in records
            ]
        }

    async def seed_default_agent(self) -> dict[str, Any]:
        store = self.store()
        resource = self.default_agent_resource()
        existing = await store.get_resource("agent_definition", self.default_agent_name)
        if existing is not None:
            return {
                "status": "exists",
                "resource": existing.model_dump(mode="json", by_alias=True, exclude_none=True),
            }

        try:
            created = await store.create_resource(resource)
        except ResourceConflictError as exc:
            raise RuntimeError(
                "Resource name is already reserved, possibly by a soft-deleted record: "
                f"{self.default_agent_name}"
            ) from exc

        return {
            "status": "created",
            "resource": created.model_dump(mode="json", by_alias=True, exclude_none=True),
        }

    def store(self) -> ResourceStore:
        return self._store

    def default_agent_resource(self) -> dict[str, Any]:
        return {
            "kind": "agent_definition",
            "name": self.default_agent_name,
            "description": "Minimal assistant for local HTTP testing.",
            "content": {
                "name": self.default_agent_name,
                "description": "Minimal assistant for local HTTP testing.",
                "systemPrompt": (
                    "You are the Agent Smith local assistant. "
                    "Keep answers concise and use the user's preferred language."
                ),
                "thinkingLevel": "high",
                "model": "gpt-5.5",
                "toolsAllow": [],
            },
        }
