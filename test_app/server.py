"""Compatibility entrypoint for the local Agent Smith HTTP test app.

Run:
    PYTHONPATH=src poetry run python test_app/server.py
"""

from __future__ import annotations

from agent_smith.transports.http.main import main
from agent_smith.transports.http.sse import json_dumps as _json_dumps

__all__ = ["_json_dumps", "main"]


if __name__ == "__main__":
    main()

