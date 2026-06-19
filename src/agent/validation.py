"""Tool argument validation for agent tools."""

from __future__ import annotations

import copy
import json

from jsonschema import ValidationError, validators

from agent_smith.ai.types import JsonObject, ToolCall
from agent_smith.agent.types import AgentTool


def _format_path(error: ValidationError) -> str:
    path = ".".join(str(part) for part in error.absolute_path)
    return path or "root"


def validate_tool_arguments(tool: AgentTool, tool_call: ToolCall) -> JsonObject:
    """Validate a tool call against its JSON Schema and return cloned arguments."""

    args = copy.deepcopy(tool_call.arguments)
    schema = tool.parameters
    validator_cls = validators.validator_for(schema)
    validator_cls.check_schema(schema)
    validator = validator_cls(schema)

    errors = sorted(validator.iter_errors(args), key=lambda error: list(error.absolute_path))
    if not errors:
        return args

    details = "\n".join(f"  - {_format_path(error)}: {error.message}" for error in errors)
    received = json.dumps(tool_call.arguments, indent=2, sort_keys=True)
    raise ValueError(
        f'Validation failed for tool "{tool_call.name}":\n'
        f"{details or 'Unknown validation error'}\n\n"
        f"Received arguments:\n{received}"
    )
