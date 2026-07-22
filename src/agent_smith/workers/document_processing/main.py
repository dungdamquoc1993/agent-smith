"""Document-processing worker command."""

import asyncio
from pathlib import Path

from agent_smith.bootstrap.document_worker import build_document_worker_container
from agent_smith.infra.config import (
    get_runtime_settings,
    load_environment,
    validate_runtime_startup,
)

REPO_ROOT = Path(__file__).resolve().parents[4]


def main() -> None:
    async def run() -> None:
        load_environment(REPO_ROOT / ".env")
        settings = get_runtime_settings()
        validate_runtime_startup(settings, require_llm=False)
        container = build_document_worker_container(settings)
        try:
            await container.check_dependencies()
            container.application.install_signal_handlers()
            print(f"Agent Smith document worker started: {container.worker.worker_id}")
            await container.application.run()
        finally:
            await container.close()

    asyncio.run(run())


if __name__ == "__main__":
    main()
