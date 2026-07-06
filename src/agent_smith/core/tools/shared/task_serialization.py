"""Serialization helpers for task tool results."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel

from agent_smith.core.tasks import TaskOutputSnapshot, TaskRecord


def serialize_value(value: Any) -> Any:
    if isinstance(value, BaseModel):
        return value.model_dump(mode="json", by_alias=True, exclude_none=True)
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, dict):
        return {str(key): serialize_value(item) for key, item in value.items()}
    if isinstance(value, list):
        return [serialize_value(item) for item in value]
    if isinstance(value, tuple):
        return [serialize_value(item) for item in value]
    return value


def task_record_to_details(record: TaskRecord) -> dict[str, Any]:
    return serialize_value(record)


def task_output_to_details(output: TaskOutputSnapshot) -> dict[str, Any]:
    return serialize_value(output)


def task_result_text(record: TaskRecord) -> str:
    result = serialize_value(record.result)
    if isinstance(result, dict):
        final_text = result.get("finalText")
        if isinstance(final_text, str):
            return final_text
    if result is None:
        return ""
    return str(result)
