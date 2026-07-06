"""Runtime specs produced from persisted agent definitions."""

from __future__ import annotations

from pydantic import BaseModel, Field

from agent_smith.core.agent.harness.types import AgentHarnessResources
from agent_smith.core.agent.types import AgentTool
from agent_smith.core.llm.types import JsonObject, Model, ModelThinkingLevel
from agent_smith.core.resources.types import AgentDefinition


class AgentRuntimeSpec(BaseModel):
    definition: AgentDefinition
    model: Model
    system_prompt: str = Field(alias="systemPrompt")
    resources: AgentHarnessResources
    tools: list[AgentTool]
    active_tool_names: list[str] = Field(alias="activeToolNames")
    thinking_level: ModelThinkingLevel = Field(alias="thinkingLevel")
    max_turns: int | None = Field(default=None, alias="maxTurns")
    permission_mode: str | None = Field(default=None, alias="permissionMode")
    mcp_server_configs: dict[str, JsonObject] = Field(
        default_factory=dict,
        alias="mcpServerConfigs",
    )

    model_config = {
        "populate_by_name": True,
        "arbitrary_types_allowed": True,
    }
