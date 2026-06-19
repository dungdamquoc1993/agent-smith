"""Shared pytest fixtures for AI integration tests."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
GCP_CREDENTIALS = ROOT / ".gcp" / "gen-lang-client-0054778016-27f8eccd342d.json"


def _load_dotenv(path: Path) -> None:
    if not path.is_file():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        key, value = stripped.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip())


@pytest.fixture(scope="session", autouse=True)
def _bootstrap_ai() -> None:
    _load_dotenv(ROOT / ".env")
    if GCP_CREDENTIALS.is_file():
        os.environ.setdefault("GOOGLE_APPLICATION_CREDENTIALS", str(GCP_CREDENTIALS))

    from ai import bootstrap_providers

    bootstrap_providers()
