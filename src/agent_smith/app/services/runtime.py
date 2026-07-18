"""HTTP runtime discovery and readiness use cases."""

from __future__ import annotations

from typing import Any

from agent_smith.app.ports.runtime import ReadinessCheck
from agent_smith.app.services.agent_runs import AgentRunService
from agent_smith.app.services.resources import ResourceService
from agent_smith.app.services.sessions import SessionService, principal_payload


class RuntimeService:
    def __init__(
        self,
        readiness: ReadinessCheck,
        sessions: SessionService,
        resources: ResourceService,
        agent_runs: AgentRunService,
        *,
        postgres_url: str,
    ) -> None:
        self._readiness = readiness
        self._sessions = sessions
        self._resources = resources
        self._agent_runs = agent_runs
        self._postgres_url = postgres_url

    async def bootstrap(self) -> dict[str, Any]:
        await self._readiness.check()
        principal = await self._sessions.ensure_principal()
        return {
            "postgres": {"ok": True, "url": self._postgres_url},
            "principal": principal_payload(principal),
            "sessions": await self._sessions.list_sessions(),
            "resources": (await self._resources.list_resources())["resources"],
            **self.model_catalog(),
        }

    def model_catalog(self) -> dict[str, Any]:
        return {
            "defaults": {
                "agentName": self._resources.default_agent_name,
                "modelKey": self._agent_runs.default_model_selection(),
            },
            "models": self._agent_runs.model_choices(),
        }
