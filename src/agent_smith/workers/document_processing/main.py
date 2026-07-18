"""Document-processing worker command."""

import asyncio
from pathlib import Path

from agent_smith.bootstrap.document_worker import build_document_worker_container
from agent_smith.infra.config import get_settings, load_environment

REPO_ROOT = Path(__file__).resolve().parents[4]


def main() -> None:
    async def run() -> None:
        load_environment(REPO_ROOT / ".env")
        container = build_document_worker_container(get_settings())
        container.application.install_signal_handlers()
        print(f"Agent Smith document worker started: {container.worker.worker_id}")
        await container.application.run()

    asyncio.run(run())


if __name__ == "__main__":
    main()
