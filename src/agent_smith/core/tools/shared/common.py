"""Shared helpers for built-in agent tools."""

from __future__ import annotations

import asyncio
import inspect
from collections.abc import Awaitable
from typing import Any

from agent_smith.core.agent.types import AbortSignal, AgentToolResult
from agent_smith.core.llm.types import HookPayload, TextContent


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


def text_result(
    text: str,
    *,
    details: HookPayload | None = None,
    terminate: bool | None = None,
) -> AgentToolResult:
    return AgentToolResult(
        content=[TextContent(text=text)],
        details=details,
        terminate=terminate,
    )


async def maybe_await(value: Any) -> Any:
    if inspect.isawaitable(value):
        return await value
    return value


async def await_with_abort(
    value: Any,
    *,
    signal: AbortSignal | None = None,
    timeout_seconds: float | None = None,
    poll_seconds: float = 0.05,
) -> Any:
    """Await a value while allowing the harness abort signal to interrupt it."""

    if not inspect.isawaitable(value):
        if is_aborted(signal):
            raise RuntimeError("Operation aborted")
        return value

    task = asyncio.ensure_future(value)
    deadline = None
    if timeout_seconds is not None:
        deadline = asyncio.get_running_loop().time() + timeout_seconds

    while True:
        if is_aborted(signal):
            task.cancel()
            raise RuntimeError("Operation aborted")

        timeout = poll_seconds
        if deadline is not None:
            remaining = deadline - asyncio.get_running_loop().time()
            if remaining <= 0:
                task.cancel()
                raise TimeoutError(f"Timed out after {timeout_seconds:g}s")
            timeout = min(timeout, remaining)

        done, _ = await asyncio.wait({task}, timeout=timeout)
        if done:
            return task.result()


MaybeAwaitable = Any | Awaitable[Any]
