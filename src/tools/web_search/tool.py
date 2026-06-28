"""Web search tool factory and provider adapters."""

from __future__ import annotations

import asyncio
import json
import os
import time
import urllib.parse
import urllib.request
from collections.abc import Callable, Mapping
from typing import Protocol

from pydantic import BaseModel, Field

from agent.types import AgentTool
from ai.types import JsonObject
from tools.shared.common import MaybeAwaitable, maybe_await, text_result
from tools.web_search.constants import WEB_SEARCH_PROVIDER_ENV, WEB_SEARCH_TOOL_NAME


class SearchResult(BaseModel):
    title: str
    url: str
    snippet: str = ""


class SearchRequest(BaseModel):
    query: str = Field(min_length=2)
    allowed_domains: list[str] | None = Field(default=None, alias="allowed_domains")
    blocked_domains: list[str] | None = Field(default=None, alias="blocked_domains")
    max_results: int = Field(default=5, ge=1, le=20, alias="max_results")

    model_config = {"populate_by_name": True}


class SearchProvider(Protocol):
    name: str
    required_env: tuple[str, ...]

    def is_configured(self, env: Mapping[str, str]) -> bool: ...

    async def search(
        self,
        request: SearchRequest,
        env: Mapping[str, str],
    ) -> list[SearchResult]: ...


JsonRequester = Callable[..., MaybeAwaitable]


class SearchProviderRegistry:
    def __init__(self, providers: list[SearchProvider] | None = None) -> None:
        resolved_providers = default_providers() if providers is None else providers
        self._providers = {provider.name: provider for provider in resolved_providers}

    def names(self) -> list[str]:
        return list(self._providers.keys())

    def configured(self, env: Mapping[str, str]) -> list[SearchProvider]:
        return [provider for provider in self._providers.values() if provider.is_configured(env)]

    def resolve(
        self,
        env: Mapping[str, str],
        provider_name: str | None = None,
    ) -> SearchProvider:
        selected_name = provider_name or env.get(WEB_SEARCH_PROVIDER_ENV)
        if selected_name:
            provider = self._providers.get(selected_name)
            if provider is None:
                raise RuntimeError(
                    f"Unknown web search provider: {selected_name}. "
                    f"Available providers: {', '.join(self.names())}"
                )
            if not provider.is_configured(env):
                missing = ", ".join(key for key in provider.required_env if not env.get(key))
                raise RuntimeError(
                    f"Web search provider {provider.name} is not configured. "
                    f"Missing env: {missing}"
                )
            return provider

        configured = self.configured(env)
        if configured:
            return configured[0]

        required = sorted({key for provider in self._providers.values() for key in provider.required_env})
        raise RuntimeError(
            "No configured web search provider found. Set one of: " + ", ".join(required)
        )


class TavilySearchProvider:
    name = "tavily"
    required_env = ("TAVILY_API_KEY",)

    def __init__(self, post_json: JsonRequester | None = None, timeout_seconds: float = 20) -> None:
        self.post_json = post_json or post_json_request
        self.timeout_seconds = timeout_seconds

    def is_configured(self, env: Mapping[str, str]) -> bool:
        return bool(env.get("TAVILY_API_KEY"))

    async def search(self, request: SearchRequest, env: Mapping[str, str]) -> list[SearchResult]:
        payload = {
            "query": request.query,
            "max_results": request.max_results,
            "include_answer": False,
        }
        data = await maybe_await(
            self.post_json(
                "https://api.tavily.com/search",
                {
                    "Authorization": f"Bearer {env['TAVILY_API_KEY']}",
                    "Content-Type": "application/json",
                },
                payload,
                self.timeout_seconds,
            )
        )
        results = data.get("results", []) if isinstance(data, dict) else []
        return [
            SearchResult(
                title=str(item.get("title") or item.get("url") or "Untitled"),
                url=str(item.get("url") or ""),
                snippet=str(item.get("content") or item.get("snippet") or ""),
            )
            for item in results
            if isinstance(item, dict) and item.get("url")
        ]


class BraveSearchProvider:
    name = "brave"
    required_env = ("BRAVE_SEARCH_API_KEY",)

    def __init__(self, get_json: JsonRequester | None = None, timeout_seconds: float = 20) -> None:
        self.get_json = get_json or get_json_request
        self.timeout_seconds = timeout_seconds

    def is_configured(self, env: Mapping[str, str]) -> bool:
        return bool(env.get("BRAVE_SEARCH_API_KEY"))

    async def search(self, request: SearchRequest, env: Mapping[str, str]) -> list[SearchResult]:
        query = urllib.parse.urlencode({"q": request.query, "count": request.max_results})
        url = f"https://api.search.brave.com/res/v1/web/search?{query}"
        data = await maybe_await(
            self.get_json(
                url,
                {
                    "Accept": "application/json",
                    "X-Subscription-Token": env["BRAVE_SEARCH_API_KEY"],
                },
                self.timeout_seconds,
            )
        )
        web_results = data.get("web", {}).get("results", []) if isinstance(data, dict) else []
        return [
            SearchResult(
                title=str(item.get("title") or item.get("url") or "Untitled"),
                url=str(item.get("url") or ""),
                snippet=str(
                    item.get("description")
                    or " ".join(item.get("extra_snippets") or [])
                    or ""
                ),
            )
            for item in web_results
            if isinstance(item, dict) and item.get("url")
        ]


def create_web_search_tool(
    registry: SearchProviderRegistry | None = None,
    provider: str | None = None,
    env: Mapping[str, str] | None = None,
) -> AgentTool:
    provider_registry = registry or SearchProviderRegistry()

    async def execute(tool_call_id, args, signal=None, on_update=None):
        _ = tool_call_id, signal, on_update
        request = SearchRequest.model_validate(args)
        active_env = os.environ if env is None else env
        selected_provider = provider_registry.resolve(active_env, provider)
        start = time.perf_counter()
        raw_results = await selected_provider.search(request, active_env)
        results = filter_results(
            raw_results,
            allowed_domains=request.allowed_domains,
            blocked_domains=request.blocked_domains,
        )
        duration_ms = int((time.perf_counter() - start) * 1000)
        details = {
            "query": request.query,
            "provider": selected_provider.name,
            "durationMs": duration_ms,
            "results": [result.model_dump(mode="python") for result in results],
        }
        return text_result(format_search_results(request.query, selected_provider.name, results), details=details)

    return AgentTool(
        name=WEB_SEARCH_TOOL_NAME,
        label="Web Search",
        description="Search the web using a configured search provider.",
        parameters={
            "type": "object",
            "properties": {
                "query": {"type": "string", "minLength": 2},
                "allowed_domains": {
                    "type": "array",
                    "items": {"type": "string", "minLength": 1},
                },
                "blocked_domains": {
                    "type": "array",
                    "items": {"type": "string", "minLength": 1},
                },
                "max_results": {
                    "type": "integer",
                    "minimum": 1,
                    "maximum": 20,
                    "default": 5,
                },
            },
            "required": ["query"],
            "additionalProperties": False,
        },
        execute=execute,
        execution_mode="parallel",
    )


def default_providers() -> list[SearchProvider]:
    return [TavilySearchProvider(), BraveSearchProvider()]


async def post_json_request(
    url: str,
    headers: dict[str, str],
    payload: JsonObject,
    timeout_seconds: float,
) -> JsonObject:
    return await asyncio.to_thread(_post_json_sync, url, headers, payload, timeout_seconds)


async def get_json_request(
    url: str,
    headers: dict[str, str],
    timeout_seconds: float,
) -> JsonObject:
    return await asyncio.to_thread(_get_json_sync, url, headers, timeout_seconds)


def _post_json_sync(
    url: str,
    headers: dict[str, str],
    payload: JsonObject,
    timeout_seconds: float,
) -> JsonObject:
    body = json.dumps(payload).encode("utf-8")
    request = urllib.request.Request(url, data=body, headers=headers, method="POST")
    with urllib.request.urlopen(request, timeout=timeout_seconds) as response:
        return json.loads(response.read().decode("utf-8"))


def _get_json_sync(url: str, headers: dict[str, str], timeout_seconds: float) -> JsonObject:
    request = urllib.request.Request(url, headers=headers, method="GET")
    with urllib.request.urlopen(request, timeout=timeout_seconds) as response:
        return json.loads(response.read().decode("utf-8"))


def filter_results(
    results: list[SearchResult],
    *,
    allowed_domains: list[str] | None,
    blocked_domains: list[str] | None,
) -> list[SearchResult]:
    return [
        result
        for result in results
        if _domain_allowed(result.url, allowed_domains)
        and not _domain_blocked(result.url, blocked_domains)
    ]


def _domain_allowed(url: str, allowed_domains: list[str] | None) -> bool:
    if not allowed_domains:
        return True
    return any(_host_matches_domain(url, domain) for domain in allowed_domains)


def _domain_blocked(url: str, blocked_domains: list[str] | None) -> bool:
    if not blocked_domains:
        return False
    return any(_host_matches_domain(url, domain) for domain in blocked_domains)


def _host_matches_domain(url: str, domain: str) -> bool:
    host = urllib.parse.urlparse(url).hostname or ""
    host = host.lower().strip(".")
    normalized_domain = domain.lower().strip(".")
    return host == normalized_domain or host.endswith(f".{normalized_domain}")


def format_search_results(query: str, provider: str, results: list[SearchResult]) -> str:
    if not results:
        return f'No web search results found for "{query}" using {provider}.'
    lines = [f'Web search results for "{query}" using {provider}:']
    for index, result in enumerate(results, start=1):
        snippet = f" - {result.snippet}" if result.snippet else ""
        lines.append(f"{index}. {result.title} ({result.url}){snippet}")
    return "\n".join(lines)
