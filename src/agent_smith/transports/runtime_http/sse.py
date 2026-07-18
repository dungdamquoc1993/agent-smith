"""Runtime SSE serialization helpers."""

from __future__ import annotations

import json
from typing import Any

from agent_smith.transports.shared_http import jsonable


def json_dumps(value: Any) -> str:
    return json.dumps(jsonable(value), ensure_ascii=False, separators=(",", ":"), default=str)


def sse_chunk(event_name: str, data: Any) -> bytes:
    return f"event: {event_name}\ndata: {json_dumps(data)}\n\n".encode("utf-8")
