"""Runtime registry for concrete agent tools."""

from __future__ import annotations

from agent_smith.agent.types import AgentTool


class ToolRegistryError(Exception):
    pass


class UnknownToolError(ToolRegistryError):
    pass


class ToolRegistry:
    def __init__(self, tools: list[AgentTool] | None = None) -> None:
        self._tools: dict[str, AgentTool] = {}
        for tool in tools or []:
            self.register(tool)

    def register(self, tool: AgentTool) -> None:
        self._tools[tool.name] = tool

    def get(self, name: str) -> AgentTool | None:
        return self._tools.get(name)

    def require(self, name: str) -> AgentTool:
        tool = self.get(name)
        if tool is None:
            raise UnknownToolError(f"Unknown tool: {name}")
        return tool

    def names(self) -> list[str]:
        return list(self._tools.keys())

    def list_tools(self, names: list[str] | None = None) -> list[AgentTool]:
        if names is None:
            return [tool.model_copy(deep=True) for tool in self._tools.values()]
        return [self.require(name).model_copy(deep=True) for name in names]

    def resolve_active_names(
        self,
        *,
        tools_allow: list[str] | None = None,
        tools_deny: list[str] | None = None,
    ) -> list[str]:
        if tools_allow is None:
            names = self.names()
        else:
            for name in tools_allow:
                self.require(name)
            names = list(tools_allow)

        deny = set(tools_deny or [])
        for name in deny:
            self.require(name)
        return [name for name in names if name not in deny]
