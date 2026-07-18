"""Container-independent HTTP response, error, and JSON request utilities."""

from __future__ import annotations

import uuid
import re
from datetime import date, datetime
from enum import Enum
from http import HTTPStatus
from typing import Any

from fastapi import Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel

_REQUEST_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")


class AgentSmithHttpError(Exception):
    def __init__(
        self,
        status_code: HTTPStatus | int,
        code: str,
        message: str,
        headers: dict[str, str] | None = None,
    ) -> None:
        super().__init__(message)
        self.status_code = int(status_code)
        self.code = code
        self.message = message
        self.headers = headers or {}


def request_id_from_header(value: str | None) -> str:
    if value and _REQUEST_ID_PATTERN.fullmatch(value):
        return value
    return str(uuid.uuid4())


def jsonable(value: Any) -> Any:
    if isinstance(value, BaseModel):
        return value.model_dump(mode="json", by_alias=True, exclude_none=True)
    if isinstance(value, uuid.UUID):
        return str(value)
    if isinstance(value, datetime | date):
        return value.isoformat()
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, (list, tuple)):
        return [jsonable(item) for item in value]
    if isinstance(value, dict):
        return {str(key): jsonable(item) for key, item in value.items()}
    return value


def json_response(data: Any, *, status_code: HTTPStatus | int = HTTPStatus.OK) -> JSONResponse:
    return JSONResponse(content=jsonable(data), status_code=int(status_code))


def error_response(
    status_code: HTTPStatus | int,
    code: str,
    message: str,
    headers: dict[str, str] | None = None,
) -> JSONResponse:
    return JSONResponse(
        content=jsonable({"error": {"code": code, "message": message}}),
        status_code=int(status_code),
        headers=headers,
    )


async def read_json_object(
    request: Request, *, invalid_status: HTTPStatus | int = HTTPStatus.BAD_REQUEST
) -> dict[str, Any]:
    raw = await request.body()
    if not raw:
        return {}
    try:
        data = await request.json()
    except ValueError as exc:
        raise AgentSmithHttpError(
            invalid_status, "invalid_request", "Invalid JSON body."
        ) from exc
    if not isinstance(data, dict):
        raise AgentSmithHttpError(invalid_status, "invalid_request", "Invalid JSON body.")
    return data
