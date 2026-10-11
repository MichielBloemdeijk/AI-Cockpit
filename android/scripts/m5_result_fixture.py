"""Disposable emulator server; fixture controls are never in the production app."""
from contextlib import asynccontextmanager
import os

from fastapi import FastAPI, Depends, HTTPException
from fastapi.responses import HTMLResponse
from sqlalchemy import select

from app.main import app as cockpit
from app.services.auth import require_auth
from app.db.session import session_scope
from app.db.tables import ResultPage
from app.services.task_service import task_service
from app.services.worker_coordinator import worker_coordinator


@asynccontextmanager
async def lifespan(_):
    async with cockpit.router.lifespan_context(cockpit):
        yield


app = FastAPI(lifespan=lifespan)
external_app = FastAPI()
worker = None


@external_app.get("/source", response_class=HTMLResponse)
async def source():
    return "<html><head><title>M5 external source</title></head><body><h1>M5-EXTERNAL-BROWSER-ONLY</h1></body></html>"


@app.post("/_fixture/publish/{task_id}", dependencies=[Depends(require_auth)])
async def publish(task_id: str):
    global worker
    if worker is None:
        worker = await worker_coordinator.activate(process_identity="m5-emulator-fake-worker")
    claimed = await worker_coordinator.claim_next(worker.worker_id)
    if claimed.task is None or claimed.task.task_id != task_id:
        raise HTTPException(409, "Fixture did not claim requested task")
    task = claimed.task
    await task_service.complete(task_id, summary="M5 native document", final_text=(
        "M5-NATIVE-DOCUMENT-READY\n\n"
        f"[External fixture source]({os.environ['M5_EXTERNAL_URL']})"), idempotency_key="m5-emulator-publish",
        attempt_id=task.attempt_id, execution_run_id=task.execution_run_id, claim_token=task.claim_token)
    await worker_coordinator.release_claim(worker.worker_id, task.attempt_id,
        claim_token=task.claim_token, writers_stopped=True)
    async with session_scope() as session:
        page = (await session.execute(select(ResultPage).where(ResultPage.task_id == task_id))).scalar_one()
        return {"url": f"/results/{page.public_id}", "revision": page.revision_number}


app.mount("/", cockpit)
