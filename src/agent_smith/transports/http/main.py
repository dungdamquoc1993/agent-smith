"""Local HTTP/SSE entrypoint for Agent Smith."""

from __future__ import annotations

import os
import warnings
from http.server import ThreadingHTTPServer
from pathlib import Path

from agent_smith.app.container import AppContainer, load_dotenv
from agent_smith.transports.http.routes import AsyncRuntime, create_handler

warnings.filterwarnings(
    "ignore",
    message=r"Valid config keys have changed in V2:.*",
    category=UserWarning,
)

HOST = os.environ.get("AGENT_SMITH_TEST_APP_HOST", "127.0.0.1")
PORT = int(os.environ.get("AGENT_SMITH_TEST_APP_PORT", "8765"))
REPO_ROOT = Path(__file__).resolve().parents[4]


def create_server(
    *,
    host: str = HOST,
    port: int = PORT,
    static_dir: Path | None = None,
) -> tuple[ThreadingHTTPServer, AsyncRuntime, AppContainer]:
    load_dotenv(REPO_ROOT / ".env")
    container = AppContainer()
    container.bootstrap_providers()
    runtime = AsyncRuntime()
    handler = create_handler(container=container, runtime=runtime, static_dir=static_dir)
    server = ThreadingHTTPServer((host, port), handler)
    return server, runtime, container


def main() -> None:
    server, runtime, container = create_server()
    print(f"Agent Smith HTTP adapter: http://{HOST}:{PORT}")
    print("Expected DB schema: poetry run alembic upgrade head")
    print(f"OPENAI_API_KEY loaded: {'yes' if os.environ.get('OPENAI_API_KEY') else 'no'}")
    print(
        "Gemma local endpoint: "
        f"{container.agent_runs.gemma_base_url} model={container.agent_runs.gemma_upstream_model}"
    )
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopping HTTP adapter...")
    finally:
        server.server_close()
        runtime.stop()


if __name__ == "__main__":
    main()
