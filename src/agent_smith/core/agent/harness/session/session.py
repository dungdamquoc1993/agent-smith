"""Session facade over append-only session storage."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from agent_smith.core.llm.types import HookPayload, JsonValue
from agent_smith.core.agent.types import AgentMessage
from agent_smith.core.agent.persistence import project_message_for_persistence
from agent_smith.core.agent.harness.session.types import (
    SessionContext,
    SessionMetadata,
    SessionStorage,
    SessionTreeEntry,
    build_session_context,
)


def now_iso() -> str:
    return datetime.now(UTC).isoformat()


class Session:
    def __init__(self, storage: SessionStorage) -> None:
        self._storage = storage

    async def get_metadata(self) -> SessionMetadata:
        return await self._storage.get_metadata()

    def get_storage(self) -> SessionStorage:
        return self._storage

    async def get_leaf_id(self) -> str | None:
        return await self._storage.get_leaf_id()

    async def get_entry(self, entry_id: str) -> SessionTreeEntry | None:
        return await self._storage.get_entry(entry_id)

    async def get_entries(self) -> list[SessionTreeEntry]:
        return await self._storage.get_entries()

    async def get_branch(self, from_id: str | None = None) -> list[SessionTreeEntry]:
        leaf_id = from_id if from_id is not None else await self._storage.get_leaf_id()
        return await self._storage.get_path_to_root(leaf_id)

    async def build_context(self) -> SessionContext:
        return build_session_context(await self.get_branch())

    async def append_entry(self, entry: SessionTreeEntry) -> str:
        await self._storage.append_entry(entry)
        return entry.id

    async def append_message(self, message: AgentMessage) -> str:
        return await self._append_typed_entry(
            {"type": "message", "message": project_message_for_persistence(message)}
        )

    async def append_thinking_level_change(self, thinking_level: str) -> str:
        return await self._append_typed_entry(
            {"type": "thinking_level_change", "thinkingLevel": thinking_level}
        )

    async def append_model_change(self, provider: str, model_id: str) -> str:
        return await self._append_typed_entry(
            {"type": "model_change", "provider": provider, "modelId": model_id}
        )

    async def append_active_tools_change(self, active_tool_names: list[str]) -> str:
        return await self._append_typed_entry(
            {
                "type": "active_tools_change",
                "activeToolNames": list(active_tool_names),
            }
        )

    async def append_compaction(
        self,
        summary: str,
        first_kept_entry_id: str,
        tokens_before: int,
        details: HookPayload | None = None,
        from_hook: bool | None = None,
    ) -> str:
        return await self._append_typed_entry(
            {
                "type": "compaction",
                "summary": summary,
                "firstKeptEntryId": first_kept_entry_id,
                "tokensBefore": tokens_before,
                "details": details,
                "fromHook": from_hook,
            }
        )

    async def append_custom_entry(self, custom_type: str, data: JsonValue | None = None) -> str:
        return await self._append_typed_entry(
            {"type": "custom", "customType": custom_type, "data": data}
        )

    async def append_custom_message_entry(
        self,
        custom_type: str,
        content: HookPayload,
        display: bool,
        details: HookPayload | None = None,
    ) -> str:
        return await self._append_typed_entry(
            {
                "type": "custom_message",
                "customType": custom_type,
                "content": content,
                "display": display,
                "details": details,
            }
        )

    async def append_label(self, target_id: str, label: str | None) -> str:
        target = await self._storage.get_entry(target_id)
        if target is None:
            raise ValueError(f"Entry {target_id} not found")
        return await self._append_typed_entry(
            {"type": "label", "targetId": target_id, "label": label}
        )

    async def append_session_name(self, name: str) -> str:
        return await self._append_typed_entry({"type": "session_info", "name": name.strip()})

    async def move_to(self, entry_id: str | None) -> None:
        if entry_id is not None and await self._storage.get_entry(entry_id) is None:
            raise ValueError(f"Entry {entry_id} not found")
        await self._storage.set_leaf_id(entry_id)

    async def _append_typed_entry(self, payload: dict[str, Any]) -> str:
        entry = SessionTreeEntry(
            **payload,
            id=await self._storage.create_entry_id(),
            parentId=await self._storage.get_leaf_id(),
            timestamp=now_iso(),
        )
        await self._storage.append_entry(entry)
        return entry.id
