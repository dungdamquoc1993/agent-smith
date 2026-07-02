"""Compile agent definitions into harness runtime options."""

from __future__ import annotations

import inspect
from collections.abc import Awaitable, Callable
from typing import TypeAlias

from agent.harness import AgentHarness, AgentHarnessOptions
from agent.harness.compaction import CompactionSettings
from agent.harness.types import (
    AgentHarnessResources,
    AgentHarnessSession,
    AgentHarnessStreamOptions,
    AgentCatalogEntry,
    GetAgentHarnessAuthFn,
)
from agent.types import AgentTool, StreamFn
from ai.models import get_model
from ai.types import MaybeAwaitable, Model
from permission import (
    CanUseTool,
    InMemoryPermissionRuleStore,
    PermissionMode,
    PermissionResolver,
    rule_provider_from_store,
)
from permission.session_context import visible_session_ids_for_rules
from agent.harness.session.types import SessionMetadata
from resources import AgentDefinition, ResourceResolver
from runtime.tool_registry import ToolRegistry, UnknownToolError
from runtime.types import AgentRuntimeSpec
from agent_mcp import McpConnectionManager

ModelResolver: TypeAlias = Callable[[AgentDefinition], MaybeAwaitable[Model | None]]


class AgentFactoryError(Exception):
    pass


class AgentFactory:
    def __init__(
        self,
        *,
        resource_resolver: ResourceResolver,
        tool_registry: ToolRegistry,
        default_model: Model,
        model_resolver: ModelResolver | None = None,
        stream_fn: StreamFn | None = None,
        get_api_key_and_headers: GetAgentHarnessAuthFn | None = None,
        stream_options: AgentHarnessStreamOptions | dict | None = None,
        compaction_settings: CompactionSettings | None = None,
        mcp_manager: McpConnectionManager | None = None,
        permission_resolver: PermissionResolver | None = None,
        permission_rule_store: InMemoryPermissionRuleStore | None = None,
        default_permission_mode: str = "default",
        can_use_tool: CanUseTool | None = None,
        session_metadata_lookup: Callable[[str], Awaitable[SessionMetadata | None]] | None = None,
    ) -> None:
        self.resource_resolver = resource_resolver
        self.tool_registry = tool_registry
        self.default_model = default_model
        self.model_resolver = model_resolver
        self.stream_fn = stream_fn
        self.get_api_key_and_headers = get_api_key_and_headers
        self.stream_options = (
            AgentHarnessStreamOptions.model_validate(stream_options)
            if isinstance(stream_options, dict)
            else stream_options
        )
        self.compaction_settings = compaction_settings
        self.mcp_manager = mcp_manager
        self.permission_resolver = permission_resolver
        self.permission_rule_store = permission_rule_store or InMemoryPermissionRuleStore()
        self.default_permission_mode = default_permission_mode
        self.can_use_tool = can_use_tool
        self.session_metadata_lookup = session_metadata_lookup

    async def build_runtime_spec(self, definition: AgentDefinition | str) -> AgentRuntimeSpec:
        resolved_definition = await self._resolve_definition(definition)
        resolved_resources = await self.resource_resolver.resolve()
        resources = self._select_harness_resources(
            resolved_definition,
            resolved_resources.harness_resources,
        )
        try:
            active_tool_names = self.tool_registry.resolve_active_names(
                tools_allow=resolved_definition.tools_allow,
                tools_deny=resolved_definition.tools_deny,
            )
        except UnknownToolError as exc:
            raise AgentFactoryError(str(exc)) from exc
        tools = self.tool_registry.list_tools(active_tool_names)
        mcp_server_configs = {
            name: resolved_resources.mcp_server_configs[name]
            for name in self._require_names(
                resolved_definition.mcp_servers,
                resolved_resources.mcp_server_configs.keys(),
                "MCP server config",
            )
        }
        return AgentRuntimeSpec(
            definition=resolved_definition,
            model=await self._resolve_model(resolved_definition),
            system_prompt=resolved_definition.system_prompt,
            resources=resources,
            tools=tools,
            active_tool_names=active_tool_names,
            thinking_level=resolved_definition.thinking_level,
            max_turns=resolved_definition.max_turns,
            permission_mode=resolved_definition.permission_mode,
            mcp_server_configs=mcp_server_configs,
        )

    async def create_options(
        self,
        definition: AgentDefinition | str,
        *,
        session: AgentHarnessSession,
        stream_fn: StreamFn | None = None,
        get_api_key_and_headers: GetAgentHarnessAuthFn | None = None,
        stream_options: AgentHarnessStreamOptions | dict | None = None,
        compaction_settings: CompactionSettings | None = None,
        is_background: bool = False,
        permission_mode_override: str | None = None,
        can_use_tool: CanUseTool | None = None,
        permission_resolver: PermissionResolver | None = None,
        permission_rule_store: InMemoryPermissionRuleStore | None = None,
    ) -> AgentHarnessOptions:
        spec = await self.build_runtime_spec(definition)
        resolved_resources = await self.resource_resolver.resolve()
        resources = spec.resources or AgentHarnessResources()
        resources = AgentHarnessResources(
            skills=resources.skills,
            prompt_templates=resources.prompt_templates,
            agent_catalog=_build_agent_catalog(resolved_resources.agent_definitions),
            user_memory=resources.user_memory,
        )
        resolved_stream_options = (
            AgentHarnessStreamOptions.model_validate(stream_options)
            if isinstance(stream_options, dict)
            else stream_options or self.stream_options
        )
        tools = list[AgentTool](spec.tools)
        active_tool_names = list[str](spec.active_tool_names)
        if self.mcp_manager is not None and spec.mcp_server_configs:
            metadata = await session.get_metadata()
            materialized = await self.mcp_manager.materialize_tools(
                spec.mcp_server_configs,
                principal_id=metadata.principal_id,
            )
            tools.extend(materialized.tools)
            active_tool_names.extend(materialized.active_tool_names)
        permission_mode = _resolve_permission_mode(
            permission_mode_override or spec.permission_mode,
            self.default_permission_mode,
            is_background=is_background,
        )
        rule_store = permission_rule_store or self.permission_rule_store
        if permission_resolver is not None or self.permission_resolver is not None:
            resolver = permission_resolver or self.permission_resolver
        else:
            visible_ids = await visible_session_ids_for_rules(
                session,
                lookup_metadata=self.session_metadata_lookup,
            )
            resolver = PermissionResolver(
                rule_provider=rule_provider_from_store(
                    rule_store,
                    visible_session_ids=visible_ids,
                ),
                default_mode=permission_mode,
            )
        return AgentHarnessOptions(
            session=session,
            model=spec.model,
            thinking_level=spec.thinking_level,
            system_prompt=spec.system_prompt,
            resources=resources,
            tools=tools,
            active_tool_names=active_tool_names,
            stream_fn=stream_fn or self.stream_fn,
            get_api_key_and_headers=get_api_key_and_headers or self.get_api_key_and_headers,
            stream_options=resolved_stream_options,
            compaction_settings=compaction_settings or self.compaction_settings,
            permission_mode=permission_mode,
            permission_resolver=resolver,
            can_use_tool=can_use_tool or self.can_use_tool,
            permission_rule_store=rule_store,
            is_background=is_background,
        )

    async def create_harness(
        self,
        definition: AgentDefinition | str,
        *,
        session: AgentHarnessSession,
        stream_fn: StreamFn | None = None,
        get_api_key_and_headers: GetAgentHarnessAuthFn | None = None,
        stream_options: AgentHarnessStreamOptions | dict | None = None,
        compaction_settings: CompactionSettings | None = None,
        is_background: bool = False,
        permission_mode_override: str | None = None,
        can_use_tool: CanUseTool | None = None,
        permission_resolver: PermissionResolver | None = None,
        permission_rule_store: InMemoryPermissionRuleStore | None = None,
    ) -> AgentHarness:
        return AgentHarness(
            await self.create_options(
                definition,
                session=session,
                stream_fn=stream_fn,
                get_api_key_and_headers=get_api_key_and_headers,
                stream_options=stream_options,
                compaction_settings=compaction_settings,
                is_background=is_background,
                permission_mode_override=permission_mode_override,
                can_use_tool=can_use_tool,
                permission_resolver=permission_resolver,
                permission_rule_store=permission_rule_store,
            )
        )

    async def _resolve_definition(self, definition: AgentDefinition | str) -> AgentDefinition:
        if isinstance(definition, AgentDefinition):
            return definition
        resolved = await self.resource_resolver.get_agent_definition(definition)
        if resolved is None:
            raise AgentFactoryError(f"Unknown agent definition: {definition}")
        return resolved

    async def _resolve_model(self, definition: AgentDefinition) -> Model:
        if self.model_resolver:
            resolved = await _maybe_await(self.model_resolver(definition))
            if resolved is not None:
                return resolved

        model_ref = definition.model
        if model_ref is None:
            return self.default_model
        if isinstance(model_ref, str):
            if model_ref in {self.default_model.id, self.default_model.name}:
                return self.default_model
            resolved = get_model(self.default_model.provider, model_ref)
            if resolved:
                return resolved
            raise AgentFactoryError(f"Unknown model: {self.default_model.provider}/{model_ref}")

        provider = model_ref.provider or self.default_model.provider
        if provider == self.default_model.provider and model_ref.model_id in {
            self.default_model.id,
            self.default_model.name,
        }:
            return self.default_model
        resolved = get_model(provider, model_ref.model_id)
        if resolved:
            return resolved
        raise AgentFactoryError(f"Unknown model: {provider}/{model_ref.model_id}")

    def _select_harness_resources(
        self,
        definition: AgentDefinition,
        resources: AgentHarnessResources,
    ) -> AgentHarnessResources:
        skills = resources.skills or []
        prompt_templates = resources.prompt_templates or []

        if definition.skills:
            skill_names = {skill.name: skill for skill in skills}
            selected_skills = [
                skill_names[name]
                for name in self._require_names(definition.skills, skill_names.keys(), "skill")
            ]
        else:
            selected_skills = list(skills)

        if definition.prompt_templates:
            template_names = {template.name: template for template in prompt_templates}
            selected_templates = [
                template_names[name]
                for name in self._require_names(
                    definition.prompt_templates,
                    template_names.keys(),
                    "prompt template",
                )
            ]
        else:
            selected_templates = list(prompt_templates)

        return AgentHarnessResources(
            skills=selected_skills,
            prompt_templates=selected_templates,
            user_memory=resources.user_memory,
        )

    def _require_names(
        self,
        requested: list[str],
        available: set[str] | list[str] | dict[str, object],
        label: str,
    ) -> list[str]:
        available_names = set(available)
        missing = [name for name in requested if name not in available_names]
        if missing:
            raise AgentFactoryError(f"Unknown {label}: {', '.join(missing)}")
        return list(requested)


async def _maybe_await(value: MaybeAwaitable[Model | None]) -> Model | None:
    if inspect.isawaitable(value):
        return await value
    return value


def _resolve_permission_mode(
    value: str | None,
    default: str,
    *,
    is_background: bool,
) -> PermissionMode:
    if value is None and is_background:
        return "accept_edits"
    resolved = value or default
    if resolved not in {"plan", "default", "accept_edits", "bypass"}:
        raise AgentFactoryError(f"Unknown permission mode: {resolved}")
    return resolved  # type: ignore[return-value]


def _build_agent_catalog(definitions: list[AgentDefinition]) -> list[AgentCatalogEntry] | None:
    if not definitions:
        return None
    return [
        AgentCatalogEntry(
            name=definition.name,
            description=definition.description,
            when_to_use=definition.when_to_use,
            tools_allow=definition.tools_allow,
            tools_deny=definition.tools_deny,
        )
        for definition in definitions
    ]
