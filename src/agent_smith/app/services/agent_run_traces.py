"""Development trace logging for agent runs.

This module is intentionally isolated from the main run service. Remove this
file and the small call sites in ``agent_runs.py`` when these local traces are
no longer needed.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from agent_smith.app.invocation import VerifiedActor
from agent_smith.core.agent.harness.session.session import Session
from agent_smith.core.agent.harness.types import BeforeAgentStartEvent, ContextEvent
from agent_smith.core.llm.types import JsonObject


LOG_DIR = Path(os.environ.get("AGENT_SMITH_TRACE_LOG_DIR", ".log/agent-runs"))


@dataclass
class AgentRunTrace:
    flow: str
    run_id: str
    session_id: str
    stable_context: JsonObject | None = None
    turn_context: JsonObject | None = None
    invocation: JsonObject | None = None
    actor: JsonObject | None = None
    system_prompt: str | None = None
    agent_start: JsonObject | None = None
    context_sequence: int = 0
    file_prefix: str = field(init=False)

    def __post_init__(self) -> None:
        timestamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S%fZ")
        run = _safe_filename(self.run_id)
        session = _safe_filename(self.session_id)
        self.file_prefix = f"{timestamp}_{self.flow}_{session}_{run}"

    async def write_context(self, event: ContextEvent) -> None:
        self.context_sequence += 1
        payload = {
            "type": "agent_context_before_llm",
            "flow": self.flow,
            "runId": self.run_id,
            "sessionId": self.session_id,
            "contextSequence": self.context_sequence,
            "createdAt": datetime.now(UTC).isoformat(),
            "stableContext": self.stable_context,
            "turnContext": self.turn_context,
            "invocation": self.invocation,
            "actor": self.actor,
            "agentStart": self.agent_start,
            "systemPrompt": self.system_prompt,
            "messages": [_jsonable_model(message) for message in event.messages],
        }
        await _write_trace_json(f"{self.file_prefix}_context.json", payload)

    async def write_session_entries(self, session: Session) -> None:
        metadata = await session.get_metadata()
        entries = await session.get_entries()
        payload = {
            "type": "session_entries",
            "flow": self.flow,
            "runId": self.run_id,
            "session": metadata.model_dump(mode="json", by_alias=True, exclude_none=True),
            "createdAt": datetime.now(UTC).isoformat(),
            "entries": [
                entry.model_dump(mode="json", by_alias=True, exclude_none=True)
                for entry in entries
            ],
        }
        await _write_trace_json(f"{self.file_prefix}_session_entries.json", payload)


def create_agent_run_trace(
    *,
    flow: str,
    run_id: str,
    session_id: str,
    stable_context: JsonObject | None = None,
    turn_context: JsonObject | None = None,
    invocation: JsonObject | None = None,
    actor: VerifiedActor | JsonObject | None = None,
) -> AgentRunTrace:
    actor_payload = actor.model_dump(mode="json", by_alias=True, exclude_none=True) if hasattr(actor, "model_dump") else actor
    return AgentRunTrace(
        flow=flow,
        run_id=run_id,
        session_id=session_id,
        stable_context=stable_context,
        turn_context=turn_context,
        invocation=invocation,
        actor=actor_payload if isinstance(actor_payload, dict) else None,
    )


def install_trace_hooks(harness: Any, trace: AgentRunTrace) -> None:
    async def before_agent_start(event: BeforeAgentStartEvent) -> None:
        trace.system_prompt = event.system_prompt
        trace.agent_start = {
            "prompt": event.prompt,
            "images": _jsonable_model(event.images),
            "resources": _jsonable_model(event.resources),
        }

    async def context(event: ContextEvent) -> None:
        await trace.write_context(event)

    harness.on("before_agent_start", before_agent_start)
    harness.on("context", context)


async def _write_trace_json(filename: str, payload: JsonObject | dict[str, Any]) -> None:
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    path = LOG_DIR / filename
    path.write_text(
        json.dumps(_jsonable_model(payload), ensure_ascii=False, indent=2, default=str),
        encoding="utf-8",
    )


def _jsonable_model(value: Any) -> Any:
    if hasattr(value, "model_dump"):
        return value.model_dump(mode="json", by_alias=True, exclude_none=True)
    if isinstance(value, list):
        return [_jsonable_model(item) for item in value]
    if isinstance(value, dict):
        return {str(key): _jsonable_model(item) for key, item in value.items()}
    return value


def _safe_filename(value: str) -> str:
    return "".join(char if char.isalnum() or char in {"-", "_"} else "_" for char in value)[:96]
