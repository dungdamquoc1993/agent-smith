"""Document-processing worker entrypoint."""

from __future__ import annotations

import asyncio
import signal
from pathlib import Path

from agent_smith.app.container import AppContainer, load_dotenv
from agent_smith.workers.agent_worker import AgentWorker


def create_worker() -> AgentWorker:
    load_dotenv(Path.cwd() / ".env")
    container = AppContainer()
    container.bootstrap_providers()
    return AgentWorker(container)


def main() -> None:
    async def run() -> None:
        worker = create_worker()
        loop = asyncio.get_running_loop()
        for name in ("SIGINT", "SIGTERM"):
            signum = getattr(signal, name, None)
            if signum is not None:
                try:
                    loop.add_signal_handler(signum, worker.stop)
                except NotImplementedError:  # Windows event loop
                    pass
        print(f"Agent Smith document worker started: {worker.worker_id}")
        await worker.run_forever()

    asyncio.run(run())


if __name__ == "__main__":
    main()
