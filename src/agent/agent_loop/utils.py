"""Small shared helpers for the agent loop package."""

from __future__ import annotations

import inspect
import time
from typing import Any

from agent_smith.agent.types import (
    AbortSignal,
    AgentEvent,
    AgentEventSink,
    AgentLoopTurnUpdate,
    AgentMessage,
)


async def get_messages(getter: Any | None) -> list[AgentMessage]:
    if getter is None:
        return []
    messages = await call(getter())
    return list(messages or [])


async def call_maybe(fn: Any | None, *args: Any) -> Any:
    if fn is None:
        return None
    return await call(fn(*args))


async def call(value: Any) -> Any:
    if inspect.isawaitable(value):
        return await value
    return value


async def emit(emit_event: AgentEventSink, event: AgentEvent) -> None:
    result = emit_event(event)
    if inspect.isawaitable(result):
        await result


def coerce_turn_update(result: AgentLoopTurnUpdate | dict[str, Any]) -> AgentLoopTurnUpdate:
    if isinstance(result, AgentLoopTurnUpdate):
        return result
    return AgentLoopTurnUpdate.model_validate(result)


def next_reasoning(current: str | None, thinking_level: str | None) -> str | None:
    if thinking_level is None:
        return current
    if thinking_level == "off":
        return None
    return thinking_level


def is_aborted(signal: AbortSignal | None) -> bool:
    if signal is None:
        return False
    aborted = getattr(signal, "aborted", None)
    if isinstance(aborted, bool):
        return aborted
    is_set = getattr(signal, "is_set", None)
    if callable(is_set):
        return bool(is_set())
    return False


def now_ms() -> int:
    return int(time.time() * 1000)
