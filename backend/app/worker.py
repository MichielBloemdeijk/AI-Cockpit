"""`python -m app.worker` entry point for the durable task worker."""
from __future__ import annotations

import asyncio
import logging

from app.config import settings
from app.db.session import close_engine, ensure_database_ready, initialize_engine, run_migrations
from app.services.task_worker import TaskWorker, task_worker


logging.basicConfig(
    level=logging.DEBUG if settings.debug else logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s — %(message)s",
)
logger = logging.getLogger("app.worker")


async def wait_for_worker_activation(
    worker: TaskWorker,
    *,
    timeout_seconds: float | None = None,
) -> None:
    """Allow an abruptly stopped predecessor's heartbeat lease to expire."""
    timeout = (
        settings.worker_stale_after_seconds + 2.0
        if timeout_seconds is None
        else timeout_seconds
    )
    deadline = asyncio.get_running_loop().time() + max(0.0, timeout)
    waiting_logged = False
    while not await worker.activate():
        remaining = deadline - asyncio.get_running_loop().time()
        if remaining <= 0:
            raise RuntimeError("Another healthy task worker is already active")
        if not waiting_logged:
            logger.warning("Waiting for the previous task worker lease to expire")
            waiting_logged = True
        await asyncio.sleep(min(1.0, remaining))


async def worker_main() -> None:
    await ensure_database_ready()
    run_migrations()
    initialize_engine(settings.database_url)
    try:
        await wait_for_worker_activation(task_worker)
        logger.info("Task worker active: %s", task_worker.worker_id)
        await task_worker.run_forever()
    finally:
        await close_engine()


def main() -> None:
    try:
        asyncio.run(worker_main())
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
