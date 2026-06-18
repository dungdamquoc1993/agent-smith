#!/usr/bin/env python3
"""Demo unified AI layer — OpenAI and Google (Gemini) via LiteLLM."""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
import time

# Allow running without install
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from agent_smith.ai import Context, UserMessage, get_model, stream
from agent_smith.ai.env_keys import is_provider_configured

ROOT = os.path.join(os.path.dirname(__file__), "..")
GCP_CREDENTIALS = os.path.join(ROOT, ".gcp", "gen-lang-client-0054778016-27f8eccd342d.json")


def _load_local_env() -> None:
    env_path = os.path.join(ROOT, ".env")
    if not os.path.isfile(env_path):
        return
    with open(env_path, encoding="utf-8") as f:
        for line in f:
            stripped = line.strip()
            if not stripped or stripped.startswith("#") or "=" not in stripped:
                continue
            key, value = stripped.split("=", 1)
            os.environ.setdefault(key.strip(), value.strip())
    if os.path.isfile(GCP_CREDENTIALS):
        os.environ.setdefault("GOOGLE_APPLICATION_CREDENTIALS", GCP_CREDENTIALS)


def print_events_header(provider: str, model_id: str) -> None:
    print(f"\n{'=' * 60}")
    print(f"Provider: {provider} | Model: {model_id}")
    print("=" * 60)


async def _run_provider_demo(provider: str, model_id: str, prompt: str) -> None:
    if not is_provider_configured(provider):
        print(f"SKIP {provider} demo: credentials not configured")
        return

    model = get_model(provider, model_id)
    assert model is not None

    context = Context(
        system_prompt="You are a helpful assistant. Reply in one short sentence.",
        messages=[UserMessage(role="user", content=prompt, timestamp=int(time.time() * 1000))],
    )

    print_events_header(provider, model.id)
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


async def run_openai_demo() -> None:
    await _run_provider_demo("openai", "gpt-4o-mini", "What is Agent Smith in one sentence?")


async def run_google_demo() -> None:
    await _run_provider_demo("google", "gemini-2.5-flash", "What is Agent Smith in one sentence?")


async def main() -> None:
    _load_local_env()

    parser = argparse.ArgumentParser(description="Agent Smith AI layer demo")
    parser.add_argument(
        "--provider",
        choices=["openai", "google", "all"],
        default="all",
        help="Which demo to run",
    )
    args = parser.parse_args()

    from agent_smith.ai import bootstrap_providers

    bootstrap_providers()

    if args.provider in ("openai", "all"):
        await run_openai_demo()

    if args.provider in ("google", "all"):
        await run_google_demo()


if __name__ == "__main__":
    asyncio.run(main())
