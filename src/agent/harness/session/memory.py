"""In-memory harness session storage."""

from __future__ import annotations

import uuid
from copy import deepcopy
from typing import Any

from agent.harness.session.session import Session
from agent.harness.session.types import (
    SessionEntryType,
    SessionMetadata,
    SessionTreeEntry,
)


class MemorySessionStorage:
    def __init__(self, metadata: SessionMetadata) -> None:
        self._metadata = metadata
        self._entries: dict[str, SessionTreeEntry] = {}
        self._order: list[str] = []
        self._leaf_id: str | None = None

    async def get_metadata(self) -> SessionMetadata:
        return self._metadata.model_copy(deep=True)

    async def create_entry_id(self) -> str:
        return str(uuid.uuid4())

    async def append_entry(self, entry: SessionTreeEntry) -> None:
        self._entries[entry.id] = entry.model_copy(deep=True)
        self._order.append(entry.id)
        self._leaf_id = entry.id

    async def get_entry(self, entry_id: str) -> SessionTreeEntry | None:
        entry = self._entries.get(entry_id)
        return entry.model_copy(deep=True) if entry else None

    async def find_entries(self, entry_type: SessionEntryType) -> list[SessionTreeEntry]:
        return [
            self._entries[entry_id].model_copy(deep=True)
            for entry_id in self._order
            if self._entries[entry_id].type == entry_type
        ]

    async def get_path_to_root(self, leaf_id: str | None) -> list[SessionTreeEntry]:
        if leaf_id is None:
            return []
        path: list[SessionTreeEntry] = []
        current_id: str | None = leaf_id
        while current_id is not None:
            entry = self._entries.get(current_id)
            if entry is None:
                break
            path.append(entry.model_copy(deep=True))
            current_id = entry.parent_id
        return list(reversed(path))

    async def get_entries(self) -> list[SessionTreeEntry]:
        return [self._entries[entry_id].model_copy(deep=True) for entry_id in self._order]

    async def get_leaf_id(self) -> str | None:
        return self._leaf_id

    async def set_leaf_id(self, entry_id: str | None) -> None:
        if entry_id is not None and entry_id not in self._entries:
            raise ValueError(f"Entry {entry_id} not found")
        self._leaf_id = entry_id

    def clone(self, metadata: SessionMetadata, leaf_id: str | None = None) -> "MemorySessionStorage":
        storage = MemorySessionStorage(metadata)
        storage._entries = deepcopy(self._entries)
        storage._order = list(self._order)
        storage._leaf_id = leaf_id if leaf_id is not None else self._leaf_id
        return storage


class MemorySessionRepo:
    def __init__(self) -> None:
        self._storages: dict[str, MemorySessionStorage] = {}

    async def create(self, **options: Any) -> Session:
        session_id = str(options.get("id") or uuid.uuid4())
        metadata = SessionMetadata(
            id=session_id,
            principal_id=options.get("principal_id"),
            title=options.get("title"),
        )
        storage = MemorySessionStorage(metadata)
        self._storages[metadata.id] = storage
        return Session(storage)

    async def open(self, metadata: SessionMetadata | dict[str, Any]) -> Session:
        resolved = (
            metadata if isinstance(metadata, SessionMetadata) else SessionMetadata.model_validate(metadata)
        )
        storage = self._storages.get(resolved.id)
        if storage is None:
            raise ValueError(f"Session {resolved.id} not found")
        return Session(storage)

    async def fork(
        self,
        source: SessionMetadata | dict[str, Any],
        **options: Any,
    ) -> Session:
        source_session = await self.open(source)
        source_storage = source_session.get_storage()
        if not isinstance(source_storage, MemorySessionStorage):
            raise TypeError("MemorySessionRepo can only fork memory sessions")
        metadata = SessionMetadata(
            id=str(options.get("id") or uuid.uuid4()),
            principal_id=options.get("principal_id") or (await source_session.get_metadata()).principal_id,
            title=options.get("title"),
        )
        leaf_id = options.get("entry_id")
        storage = source_storage.clone(metadata, leaf_id=leaf_id)
        self._storages[metadata.id] = storage
        return Session(storage)
