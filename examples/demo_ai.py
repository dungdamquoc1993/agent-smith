#!/usr/bin/env python3
"""Demo unified AI layer - faux offline and OpenAI live."""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
import time

# Allow running without install
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from agent_smith.ai import (
    Context,
    Tool,
    UserMessage,
    complete,
    faux_response,
    faux_text,
    faux_thinking,
    faux_tool_call,
    get_model,
    set_faux_responses,
    stream,
)


def print_events_header(provider: str, model_id: str) -> None:
    print(f"\n{'=' * 60}")
    print(f"Provider: {provider} | Model: {model_id}")
    print("=" * 60)


async def run_faux_demo() -> None:
    from agent_smith.ai.providers.faux import register_faux_provider

    register_faux_provider()
    model = get_model("faux", "faux-1")
    assert model is not None

    set_faux_responses(
        [
            faux_response(
                [
                    faux_thinking("Let me think about this..."),
                    faux_text("Hello from Agent Smith faux provider!"),
                ]
            )
        ]
    )

    context = Context(
        system_prompt="You are a helpful assistant.",
        messages=[UserMessage(role="user", content="Say hello.", timestamp=int(time.time() * 1000))],
    )

    print_events_header("faux", model.id)
    s = stream(model, context)
    async for event in s:
        if event.type == "start":
            print("[start]")
        elif event.type == "thinking_delta":
            print(event.delta, end="", flush=True)
        elif event.type == "thinking_end":
            print("\n[thinking complete]")
        elif event.type == "text_delta":
            print(event.delta, end="", flush=True)
        elif event.type == "text_end":
            print("\n[text complete]")
        elif event.type == "done":
            print(f"\n[done] reason={event.reason}")
        elif event.type == "error":
            print(f"\n[error] {event.error.error_message}")

    final = await s.result()
    print(f"stop_reason={final.stop_reason} tokens={final.usage.total_tokens}")


async def run_faux_tool_demo() -> None:
    from agent_smith.ai.providers.faux import register_faux_provider

    register_faux_provider()
    model = get_model("faux", "faux-1")
    assert model is not None

    set_faux_responses(
        [
            faux_response(
                [faux_tool_call("get_time", {"timezone": "UTC"})],
                stop_reason="toolUse",
            )
        ]
    )

    context = Context(
        messages=[UserMessage(role="user", content="What time is it?", timestamp=int(time.time() * 1000))],
        tools=[
            Tool(
                name="get_time",
                description="Get current time",
                parameters={
                    "type": "object",
                    "properties": {"timezone": {"type": "string"}},
                },
            )
        ],
    )

    print_events_header("faux (tool)", model.id)
    final = await complete(model, context)
    for block in final.content:
        if block.type == "toolCall":
            print(f"tool_call: {block.name}({block.arguments})")
    print(f"stop_reason={final.stop_reason}")


async def run_openai_demo() -> None:
    model = get_model("openai", "gpt-4o-mini")
    assert model is not None

    if not os.environ.get("OPENAI_API_KEY"):
        print("SKIP OpenAI demo: OPENAI_API_KEY not set in environment")
        return

    context = Context(
        system_prompt="You are a helpful assistant. Reply in one short sentence.",
        messages=[
            UserMessage(
                role="user",
                content="What is Agent Smith in one sentence?",
                timestamp=int(time.time() * 1000),
            )
        ],
    )

    print_events_header("openai (litellm)", model.id)
    s = stream(model, context)
    async for event in s:
        if event.type == "text_delta":
            print(event.delta, end="", flush=True)
        elif event.type == "done":
            print(f"\n[done] reason={event.reason}")
        elif event.type == "error":
            print(f"\n[error] {event.error.error_message}")

    final = await s.result()
    print(f"stop_reason={final.stop_reason} tokens={final.usage.total_tokens}")


async def main() -> None:
    parser = argparse.ArgumentParser(description="Agent Smith AI layer demo")
    parser.add_argument(
        "--provider",
        choices=["faux", "openai", "all"],
        default="all",
        help="Which demo to run",
    )
    args = parser.parse_args()

    if args.provider in ("faux", "all"):
        await run_faux_demo()
        await run_faux_tool_demo()

    if args.provider in ("openai", "all"):
        from agent_smith.ai import bootstrap_providers

        bootstrap_providers()
        await run_openai_demo()


if __name__ == "__main__":
    asyncio.run(main())
