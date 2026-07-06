"""HTTP serialization helpers."""

from __future__ import annotations

import json
import uuid
from typing import Any

from pydantic import BaseModel


def jsonable(value: Any) -> Any:
    if isinstance(value, BaseModel):
        return value.model_dump(mode="json", by_alias=True, exclude_none=True)
    if isinstance(value, uuid.UUID):
        return str(value)
    if isinstance(value, list):
        return [jsonable(item) for item in value]
    if isinstance(value, tuple):
        return [jsonable(item) for item in value]
    if isinstance(value, dict):
        return {str(key): jsonable(item) for key, item in value.items()}
    return value


def json_dumps(value: Any) -> str:
    return json.dumps(jsonable(value), ensure_ascii=False, separators=(",", ":"), default=str)


def sse_chunk(event_name: str, data: Any) -> bytes:
    return f"event: {event_name}\ndata: {json_dumps(data)}\n\n".encode("utf-8")

