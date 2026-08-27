"""`python -m app.worker` entry point for the durable task worker."""
from __future__ import annotations

import asyncio
import logging

from app.config import settings
from app.db.session import close_engine, ensure_database_ready, initialize_engine, run_migrations
from app.services.task_worker import task_worker


logging.basicConfig(
    level=logging.DEBUG if settings.debug else logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s — %(message)s",
)
logger = logging.getLogger("app.worker")


async def worker_main() -> None:
    await ensure_database_ready()
    run_migrations()
    initialize_engine(settings.database_url)
    try:
        if not await task_worker.activate():
            raise RuntimeError("Another healthy task worker is already active")
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
