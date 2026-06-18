"""Harness resource formatting helpers."""

from __future__ import annotations

import re
from html import escape
from pathlib import PurePosixPath

from agent_smith.agent.harness.types import PromptTemplate, Skill


def format_skill_invocation(skill: Skill, additional_instructions: str | None = None) -> str:
    skill_dir = str(PurePosixPath(skill.file_path).parent)
    content = (
        f'<skill name="{skill.name}" location="{skill.file_path}">\n'
        f"References are relative to {skill_dir}.\n\n"
        f"{skill.content}\n"
        "</skill>"
    )
    return f"{content}\n\n{additional_instructions}" if additional_instructions else content


def format_skills_for_system_prompt(skills: list[Skill]) -> str:
    visible_skills = [skill for skill in skills if not skill.disable_model_invocation]
    if not visible_skills:
        return ""

    lines = [
        "The following skills provide specialized instructions for specific tasks.",
        "Read the full skill file when the task matches its description.",
        "When a skill file references a relative path, resolve it against the skill directory (parent of SKILL.md / dirname of the path) and use that absolute path in tool commands.",
        "",
        "<available_skills>",
    ]
    for skill in visible_skills:
        lines.extend(
            [
                "  <skill>",
                f"    <name>{escape(skill.name)}</name>",
                f"    <description>{escape(skill.description)}</description>",
                f"    <location>{escape(skill.file_path)}</location>",
                "  </skill>",
            ]
        )
    lines.append("</available_skills>")
    return "\n".join(lines)


def parse_command_args(args_string: str) -> list[str]:
    args: list[str] = []
    current = ""
    quote: str | None = None
    for char in args_string:
        if quote:
            if char == quote:
                quote = None
            else:
                current += char
        elif char in ('"', "'"):
            quote = char
        elif char in (" ", "\t"):
            if current:
                args.append(current)
                current = ""
        else:
            current += char
    if current:
        args.append(current)
    return args


def substitute_args(content: str, args: list[str]) -> str:
    def replace_number(match: re.Match[str]) -> str:
        index = int(match.group(1)) - 1
        return args[index] if 0 <= index < len(args) else ""

    def replace_slice(match: re.Match[str]) -> str:
        start = max(int(match.group(1)) - 1, 0)
        length = match.group(2)
        if length is not None:
            return " ".join(args[start : start + int(length)])
        return " ".join(args[start:])

    result = re.sub(r"\$(\d+)", replace_number, content)
    result = re.sub(r"\$\{@:(\d+)(?::(\d+))?\}", replace_slice, result)
    all_args = " ".join(args)
    result = result.replace("$ARGUMENTS", all_args)
    return result.replace("$@", all_args)


def format_prompt_template_invocation(
    template: PromptTemplate,
    args: list[str] | None = None,
) -> str:
    return substitute_args(template.content, args or [])
