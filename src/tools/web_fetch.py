"""Web fetch tool factory."""

from __future__ import annotations

import asyncio
import html
import re
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable

from pydantic import BaseModel, Field

from agent.types import AgentTool
from tools._common import MaybeAwaitable, maybe_await, text_result

WEB_FETCH_TOOL_NAME = "web_fetch"
DEFAULT_MAX_CHARS = 50_000


class WebFetchResponse(BaseModel):
    url: str
    final_url: str = Field(alias="finalUrl")
    status: int
    reason: str = ""
    content_type: str = Field(default="application/octet-stream", alias="contentType")
    body: bytes
    truncated: bool = False

    model_config = {"populate_by_name": True}


WebFetcher = Callable[[str, float, int], MaybeAwaitable]


def create_web_fetch_tool(
    fetcher: WebFetcher | None = None,
    timeout_seconds: float = 20,
    max_bytes: int = 1_000_000,
) -> AgentTool:
    resolved_fetcher = fetcher or default_fetcher

    async def execute(tool_call_id, args, signal=None, on_update=None):
        _ = tool_call_id, signal, on_update
        url = str(args["url"])
        max_chars = int(args.get("max_chars") or DEFAULT_MAX_CHARS)
        _validate_http_url(url)

        raw_response = resolved_fetcher(url, timeout_seconds, max_bytes)
        response = WebFetchResponse.model_validate(await maybe_await(raw_response))
        body = response.body
        truncated = response.truncated
        if len(body) > max_bytes:
            body = body[:max_bytes]
            truncated = True

        text = extract_text(body, response.content_type)
        if len(text) > max_chars:
            text = text[:max_chars]
            truncated = True

        details = {
            "url": response.url,
            "finalUrl": response.final_url,
            "status": response.status,
            "reason": response.reason,
            "contentType": response.content_type,
            "bytes": len(response.body),
            "truncated": truncated,
        }
        return text_result(
            f"Fetched {response.final_url} ({response.status}).\n\n{text}",
            details=details,
        )

    return AgentTool(
        name=WEB_FETCH_TOOL_NAME,
        label="Web Fetch",
        description="Fetch a public HTTP(S) URL and return extracted text content.",
        parameters={
            "type": "object",
            "properties": {
                "url": {
                    "type": "string",
                    "description": "The HTTP or HTTPS URL to fetch.",
                },
                "max_chars": {
                    "type": "integer",
                    "minimum": 1,
                    "maximum": DEFAULT_MAX_CHARS,
                    "description": "Optional maximum number of text characters to return.",
                },
            },
            "required": ["url"],
            "additionalProperties": False,
        },
        execute=execute,
        execution_mode="parallel",
    )


async def default_fetcher(url: str, timeout_seconds: float, max_bytes: int) -> WebFetchResponse:
    return await asyncio.to_thread(_fetch_sync, url, timeout_seconds, max_bytes)


def _fetch_sync(url: str, timeout_seconds: float, max_bytes: int) -> WebFetchResponse:
    _validate_http_url(url)
    request = urllib.request.Request(
        url,
        headers={"User-Agent": "agent-smith/0.1 (+https://github.com)"},
    )
    try:
        response = urllib.request.urlopen(request, timeout=timeout_seconds)
    except urllib.error.HTTPError as exc:
        response = exc

    with response:
        body = response.read(max_bytes + 1)
        truncated = len(body) > max_bytes
        if truncated:
            body = body[:max_bytes]
        content_type = response.headers.get("content-type", "application/octet-stream")
        return WebFetchResponse(
            url=url,
            finalUrl=response.geturl(),
            status=getattr(response, "status", response.getcode()),
            reason=getattr(response, "reason", ""),
            contentType=content_type,
            body=body,
            truncated=truncated,
        )


def _validate_http_url(url: str) -> None:
    parsed = urllib.parse.urlparse(url)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ValueError("url must be an absolute http or https URL")


def extract_text(body: bytes, content_type: str) -> str:
    charset_match = re.search(r"charset=([^;\s]+)", content_type, flags=re.IGNORECASE)
    charset = charset_match.group(1) if charset_match else "utf-8"
    text = body.decode(charset, errors="replace")
    if "html" not in content_type.lower():
        return text.strip()

    text = re.sub(r"(?is)<(script|style).*?>.*?</\1>", " ", text)
    text = re.sub(r"(?s)<[^>]+>", " ", text)
    text = html.unescape(text)
    text = re.sub(r"\s+", " ", text)
    return text.strip()
