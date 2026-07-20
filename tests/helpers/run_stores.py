"""Agent-run store test doubles."""

from __future__ import annotations

from dataclasses import asdict
from typing import Any

from agent_smith.core.runtime.run_store import (
    AgentRunFinish,
    AgentRunStart,
    AgentRunStoreError,
    LlmCallFinish,
    LlmCallStart,
)


class MemoryAgentRunStore:
    def __init__(self) -> None:
        self.runs: dict[str, dict[str, Any]] = {}
        self.calls: dict[str, dict[str, Any]] = {}

    async def start_run(self, run: AgentRunStart) -> None:
        existing = self.runs.get(run.id)
        if existing is not None:
            return
        self.runs[run.id] = {**asdict(run), "status": "running", "recording_status": "pending"}

    async def finish_run(self, finish: AgentRunFinish) -> None:
        row = self.runs.get(finish.run_id)
        if row is None:
            raise AgentRunStoreError(f"Agent run {finish.run_id} was not started")
        row.update(asdict(finish))

    async def start_call(self, call: LlmCallStart) -> None:
        existing = self.calls.get(call.id)
        if existing is not None:
            return
        if call.run_id not in self.runs:
            raise AgentRunStoreError(f"Agent run {call.run_id} was not started")
        if any(
            row["run_id"] == call.run_id and row["sequence"] == call.sequence
            for row in self.calls.values()
        ):
            raise AgentRunStoreError(
                f"LLM call sequence {call.sequence} already exists for run {call.run_id}"
            )
        self.calls[call.id] = {**asdict(call), "status": "started", "session_entry_id": None}

    async def finish_call(self, finish: LlmCallFinish) -> None:
        row = self.calls.get(finish.call_id)
        if row is None:
            raise AgentRunStoreError(f"LLM call {finish.call_id} was not started")
        row.update(asdict(finish))

    async def link_call_session_entry(self, call_id: str, session_entry_id: str) -> None:
        row = self.calls.get(call_id)
        if row is None:
            raise AgentRunStoreError(f"LLM call {call_id} was not started")
        row["session_entry_id"] = session_entry_id
