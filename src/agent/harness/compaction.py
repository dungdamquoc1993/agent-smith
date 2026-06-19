"""Compaction helpers for the stateful harness."""

from __future__ import annotations

import math
from typing import Any

from pydantic import BaseModel, Field

from agent_smith.ai.types import (
    AssistantMessage,
    Message,
    TextContent,
    ToolCall,
    ToolResultMessage,
    UserMessage,
)
from agent_smith.agent.types import AgentMessage
from agent_smith.agent.harness.session.types import SessionTreeEntry

COMPACTION_SUMMARY_PREFIX = "The conversation history before this point was compacted into the following summary:\n\n<summary>\n"
COMPACTION_SUMMARY_SUFFIX = "\n</summary>"
MICROCOMPACT_MARKER = "[Old tool result content cleared]"


class MicrocompactSettings(BaseModel):
    enabled: bool = True
    tool_result_max_chars: int = Field(default=2_000, alias="toolResultMaxChars")
    keep_recent_tool_results: int = Field(default=5, alias="keepRecentToolResults")

    model_config = {"populate_by_name": True}


class CompactionSettings(BaseModel):
    enabled: bool = True
    reserve_tokens: int = Field(default=16_384, alias="reserveTokens")
    keep_recent_tokens: int = Field(default=20_000, alias="keepRecentTokens")
    microcompact: MicrocompactSettings = Field(default_factory=MicrocompactSettings)
    max_consecutive_failures: int = Field(default=3, alias="maxConsecutiveFailures")

    model_config = {"populate_by_name": True}


class CompactionDetails(BaseModel):
    read_files: list[str] = Field(default_factory=list, alias="readFiles")
    modified_files: list[str] = Field(default_factory=list, alias="modifiedFiles")

    model_config = {"populate_by_name": True}


class CompactionPreparation(BaseModel):
    first_kept_entry_id: str = Field(alias="firstKeptEntryId")
    messages_to_summarize: list[AgentMessage] = Field(alias="messagesToSummarize")
    tokens_before: int = Field(alias="tokensBefore")
    previous_summary: str | None = Field(default=None, alias="previousSummary")
    settings: CompactionSettings

    model_config = {"populate_by_name": True}


class CompactionResult(BaseModel):
    summary: str
    first_kept_entry_id: str = Field(alias="firstKeptEntryId")
    tokens_before: int = Field(alias="tokensBefore")
    details: dict[str, Any] | None = None

    model_config = {"populate_by_name": True}


def default_compaction_settings() -> CompactionSettings:
    return CompactionSettings()


def compaction_summary_message(summary: str, timestamp: int) -> UserMessage:
    return UserMessage(
        content=COMPACTION_SUMMARY_PREFIX + summary + COMPACTION_SUMMARY_SUFFIX,
        timestamp=timestamp,
    )


def estimate_tokens(message: AgentMessage) -> int:
    chars = 0
    if message.role == "user":
        chars += _estimate_user_content_chars(message.content)
    elif message.role == "assistant":
        for block in message.content:
            if isinstance(block, TextContent):
                chars += len(block.text)
            elif isinstance(block, ToolCall):
                chars += len(block.name) + len(_safe_repr(block.arguments))
            else:
                chars += len(getattr(block, "thinking", ""))
    elif message.role == "toolResult":
        chars += _estimate_tool_result_chars(message)
    return math.ceil(chars / 4)


def estimate_context_tokens(messages: list[AgentMessage]) -> int:
    last_usage: tuple[int, int] | None = None
    for index in range(len(messages) - 1, -1, -1):
        message = messages[index]
        if message.role == "assistant" and message.stop_reason not in ("aborted", "error"):
            usage = message.usage
            tokens = usage.total_tokens or usage.input + usage.output + usage.cache_read + usage.cache_write
            if tokens > 0:
                last_usage = (index, tokens)
                break

    if not last_usage:
        return sum(estimate_tokens(message) for message in messages)

    index, tokens = last_usage
    return tokens + sum(estimate_tokens(message) for message in messages[index + 1 :])


def should_compact(context_tokens: int, context_window: int, settings: CompactionSettings) -> bool:
    if not settings.enabled:
        return False
    return context_tokens > context_window - settings.reserve_tokens


def microcompact_messages(
    messages: list[AgentMessage],
    settings: MicrocompactSettings,
) -> list[AgentMessage]:
    if not settings.enabled:
        return [message.model_copy(deep=True) for message in messages]

    tool_result_indexes = [
        index for index, message in enumerate(messages) if message.role == "toolResult"
    ]
    keep_recent = max(settings.keep_recent_tool_results, 0)
    keep = set(tool_result_indexes[-keep_recent:]) if keep_recent else set()
    result: list[AgentMessage] = []
    for index, message in enumerate(messages):
        if message.role != "toolResult" or index in keep:
            result.append(message.model_copy(deep=True))
            continue
        text = _tool_result_text(message)
        if len(text) <= settings.tool_result_max_chars:
            result.append(message.model_copy(deep=True))
            continue
        details = dict(message.details or {}) if isinstance(message.details, dict) else {}
        existing_microcompact = details.get("microcompact")
        if not isinstance(existing_microcompact, dict):
            existing_microcompact = {}
        details["microcompact"] = {
            **existing_microcompact,
            "originalChars": len(text),
            "truncated": True,
        }
        result.append(
            message.model_copy(
                deep=True,
                update={
                    "content": [TextContent(text=MICROCOMPACT_MARKER)],
                    "details": details,
                },
            )
        )
    return result


def prepare_compaction(
    path_entries: list[SessionTreeEntry],
    settings: CompactionSettings,
) -> CompactionPreparation | None:
    if not path_entries or path_entries[-1].type == "compaction":
        return None

    previous_index = _last_compaction_index(path_entries)
    previous_summary: str | None = None
    boundary_start = 0
    if previous_index >= 0:
        previous = path_entries[previous_index]
        previous_summary = previous.summary
        if previous.first_kept_entry_id:
            kept_index = _entry_index(path_entries, previous.first_kept_entry_id)
            boundary_start = kept_index if kept_index >= 0 else previous_index + 1
        else:
            boundary_start = previous_index + 1

    boundary_end = len(path_entries)
    tokens_before = estimate_context_tokens(build_projected_messages(path_entries))
    cut_index = find_api_round_cut_point(
        path_entries,
        boundary_start,
        boundary_end,
        settings.keep_recent_tokens,
    )
    if cut_index >= boundary_end:
        return None
    cut_index = adjust_cut_to_preserve_tool_pairs(path_entries, cut_index, boundary_end)
    if cut_index <= boundary_start and previous_summary is None:
        return None
    first_kept = path_entries[cut_index]
    if not first_kept.id:
        return None

    messages_to_summarize = [
        message
        for entry in path_entries[boundary_start:cut_index]
        if (message := message_from_entry_for_summary(entry)) is not None
    ]
    if not messages_to_summarize and previous_summary:
        return None

    return CompactionPreparation(
        first_kept_entry_id=first_kept.id,
        messages_to_summarize=messages_to_summarize,
        tokens_before=tokens_before,
        previous_summary=previous_summary,
        settings=settings,
    )


def build_projected_messages(path_entries: list[SessionTreeEntry]) -> list[AgentMessage]:
    latest_compaction_index = _last_compaction_index(path_entries)
    if latest_compaction_index < 0:
        return [
            message
            for entry in path_entries
            if (message := message_from_entry(entry)) is not None
        ]

    compaction = path_entries[latest_compaction_index]
    messages: list[AgentMessage] = []
    if compaction.summary:
        messages.append(
            compaction_summary_message(
                compaction.summary,
                _timestamp_ms(compaction.timestamp),
            )
        )

    retained_start = latest_compaction_index + 1
    if compaction.first_kept_entry_id:
        kept_index = _entry_index(path_entries, compaction.first_kept_entry_id)
        if 0 <= kept_index < latest_compaction_index:
            retained_start = kept_index

    for entry in path_entries[retained_start:latest_compaction_index]:
        message = message_from_entry(entry)
        if message is not None:
            messages.append(message)
    for entry in path_entries[latest_compaction_index + 1 :]:
        message = message_from_entry(entry)
        if message is not None:
            messages.append(message)
    return messages


def find_api_round_cut_point(
    entries: list[SessionTreeEntry],
    start_index: int,
    end_index: int,
    keep_recent_tokens: int,
) -> int:
    groups = group_entries_by_api_round(entries[start_index:end_index])
    if not groups:
        return start_index

    kept_tokens = 0
    cut_group = len(groups) - 1
    for index in range(len(groups) - 1, -1, -1):
        kept_tokens += sum(
            estimate_tokens(message)
            for entry in groups[index]
            if (message := message_from_entry(entry)) is not None
        )
        cut_group = index
        if kept_tokens >= keep_recent_tokens:
            break
    return start_index + sum(len(group) for group in groups[:cut_group])


def group_entries_by_api_round(entries: list[SessionTreeEntry]) -> list[list[SessionTreeEntry]]:
    groups: list[list[SessionTreeEntry]] = []
    current: list[SessionTreeEntry] = []
    last_assistant_id: str | None = None
    for entry in entries:
        assistant_id = _assistant_round_id(entry)
        if assistant_id and assistant_id != last_assistant_id and current:
            groups.append(current)
            current = [entry]
        else:
            current.append(entry)
        if assistant_id:
            last_assistant_id = assistant_id
    if current:
        groups.append(current)
    return groups


def adjust_cut_to_preserve_tool_pairs(
    entries: list[SessionTreeEntry],
    cut_index: int,
    end_index: int,
) -> int:
    if cut_index <= 0 or cut_index >= end_index:
        return cut_index
    kept_tool_results = {
        entry.message.tool_call_id
        for entry in entries[cut_index:end_index]
        if entry.type == "message" and entry.message is not None and entry.message.role == "toolResult"
    }
    if not kept_tool_results:
        return cut_index
    existing_tool_calls = _tool_call_ids(entries[cut_index:end_index])
    needed = kept_tool_results - existing_tool_calls
    while needed and cut_index > 0:
        cut_index -= 1
        needed -= _tool_call_ids([entries[cut_index]])
    return cut_index


def message_from_entry(entry: SessionTreeEntry) -> AgentMessage | None:
    if entry.type == "message":
        return entry.message
    return None


def message_from_entry_for_summary(entry: SessionTreeEntry) -> AgentMessage | None:
    if entry.type == "compaction":
        return None
    return message_from_entry(entry)


def serialize_conversation(messages: list[Message]) -> str:
    parts: list[str] = []
    for message in messages:
        if isinstance(message, UserMessage):
            text = _user_text(message)
            if text:
                parts.append(f"[User]: {text}")
        elif isinstance(message, AssistantMessage):
            text_parts: list[str] = []
            tool_parts: list[str] = []
            for block in message.content:
                if isinstance(block, TextContent):
                    text_parts.append(block.text)
                elif isinstance(block, ToolCall):
                    tool_parts.append(f"{block.name}({_safe_repr(block.arguments)})")
                else:
                    thinking = getattr(block, "thinking", "")
                    if thinking:
                        parts.append(f"[Assistant thinking]: {thinking}")
            if text_parts:
                parts.append(f"[Assistant]: {' '.join(text_parts)}")
            if tool_parts:
                parts.append(f"[Assistant tool calls]: {'; '.join(tool_parts)}")
        elif isinstance(message, ToolResultMessage):
            text = _tool_result_text(message)
            if text:
                parts.append(f"[Tool result]: {text[:2_000]}")
    return "\n\n".join(parts)


def summarization_prompt(preparation: CompactionPreparation) -> str:
    conversation = serialize_conversation(preparation.messages_to_summarize)
    previous = (
        f"\n\n<previous-summary>\n{preparation.previous_summary}\n</previous-summary>"
        if preparation.previous_summary
        else ""
    )
    return f"<conversation>\n{conversation}\n</conversation>{previous}\n\n{SUMMARIZATION_PROMPT}"


SUMMARIZATION_SYSTEM_PROMPT = (
    "You are a context summarization assistant. Read the conversation and produce only a "
    "structured summary that another LLM can use to continue the work."
)

SUMMARIZATION_PROMPT = """Create a structured context checkpoint summary.

Use this format:

## Goal
[What the user is trying to accomplish.]

## Constraints & Preferences
- [User constraints, preferences, or requirements.]

## Progress
### Done
- [x] [Completed work.]

### In Progress
- [ ] [Current work.]

### Blocked
- [Blockers, if any.]

## Key Decisions
- **[Decision]**: [Brief rationale.]

## Next Steps
1. [What should happen next.]

## Critical Context
- [Important file paths, APIs, errors, or details needed to continue.]
"""


def _last_compaction_index(entries: list[SessionTreeEntry]) -> int:
    for index in range(len(entries) - 1, -1, -1):
        if entries[index].type == "compaction":
            return index
    return -1


def _entry_index(entries: list[SessionTreeEntry], entry_id: str) -> int:
    for index, entry in enumerate(entries):
        if entry.id == entry_id:
            return index
    return -1


def _assistant_round_id(entry: SessionTreeEntry) -> str | None:
    if entry.type == "message" and entry.message is not None and entry.message.role == "assistant":
        return entry.message.response_id or f"assistant:{entry.id}"
    return None


def _tool_call_ids(entries: list[SessionTreeEntry]) -> set[str]:
    ids: set[str] = set()
    for entry in entries:
        if entry.type != "message" or entry.message is None or entry.message.role != "assistant":
            continue
        for block in entry.message.content:
            if isinstance(block, ToolCall):
                ids.add(block.id)
    return ids


def _estimate_user_content_chars(content: Any) -> int:
    if isinstance(content, str):
        return len(content)
    chars = 0
    for block in content or []:
        if isinstance(block, TextContent):
            chars += len(block.text)
        elif getattr(block, "type", None) == "image":
            chars += 4_800
    return chars


def _estimate_tool_result_chars(message: ToolResultMessage) -> int:
    return sum(len(block.text) for block in message.content if isinstance(block, TextContent))


def _tool_result_text(message: ToolResultMessage) -> str:
    return "\n".join(block.text for block in message.content if isinstance(block, TextContent))


def _user_text(message: UserMessage) -> str:
    if isinstance(message.content, str):
        return message.content
    return "\n".join(block.text for block in message.content if isinstance(block, TextContent))


def _safe_repr(value: Any) -> str:
    try:
        import json

        return json.dumps(value, sort_keys=True)
    except Exception:
        return repr(value)


def _timestamp_ms(timestamp: str) -> int:
    from datetime import datetime

    try:
        return int(datetime.fromisoformat(timestamp).timestamp() * 1000)
    except ValueError:
        return 0
