"""`python -m app.worker` entry point for the durable task worker."""
from __future__ import annotations

import argparse
import asyncio
import logging

from app.config import settings
from app.db.session import close_engine, ensure_database_ready, initialize_engine, run_migrations
from app.services.task_pool_supervisor import task_pool_supervisor
from app.services.task_worker import TaskWorker


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


async def worker_main(
    *,
    child: bool = False,
    generation: int = 1,
    process_identity: str | None = None,
    parent_pid: int | None = None,
) -> None:
    if child:
        # Install the fail-closed process boundary before touching the database
        # or doing any other child startup work. A supervisor crash during
        # migrations must not leave an uncontained writer behind.
        from app.services.windows_job import contain_current_process_tree, exit_when_parent_exits

        contain_current_process_tree()
        if parent_pid is None:
            raise RuntimeError("A supervised task child requires its parent's process identity")
        exit_when_parent_exits(parent_pid)
    await ensure_database_ready()
    run_migrations()
    initialize_engine(settings.database_url)
    try:
        if child:
            worker = TaskWorker(externally_supervised=True)
            activated = await worker.activate(
                process_identity=process_identity,
                supervisor_generation=generation,
            )
            if not activated:
                return
            logger.info("Task worker child active: %s", worker.worker_id)
            await worker.run_child()
        elif settings.task_pool_enabled:
            logger.info("Task pool supervisor starting")
            await task_pool_supervisor.run()
        else:
            if settings.task_executor == "codex":
                raise RuntimeError("Codex task execution requires the supervised process-tree pool")
            worker = TaskWorker()
            await wait_for_worker_activation(worker)
            logger.info("Legacy task worker active: %s", worker.worker_id)
            await worker.run_forever()
    finally:
        await close_engine()


def main() -> None:
    parser = argparse.ArgumentParser(description="AI Cockpit task pool")
    parser.add_argument("--child", action="store_true")
    parser.add_argument("--generation", type=int, default=1)
    parser.add_argument("--identity")
    parser.add_argument("--parent-pid", type=int)
    args = parser.parse_args()
    try:
        asyncio.run(
            worker_main(
                child=args.child,
                generation=args.generation,
                process_identity=args.identity,
                parent_pid=args.parent_pid,
            )
        )
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
