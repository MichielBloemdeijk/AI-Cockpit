"""FastAPI application entry point."""
from __future__ import annotations

from contextlib import asynccontextmanager
import logging
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from app.config import settings
from app.api import apps, auth, chat, devices, jarvis, memory, notifications, retrieval, tasks
from app.api import conversations, knowledge
from app.db.session import close_engine, ensure_database_ready, initialize_engine, run_migrations
from app.services.conversation_store import conversation_store
from app.services.run_supervisor import run_supervisor
from app.services.task_notifications import recover_pending_task_notifications
from app.services.reminder_scheduler import reminder_scheduler
from app.services.jarvis_coordinator import jarvis_coordinator
from app.services.jarvis_live_telemetry import jarvis_live_telemetry
from app.services.openai_live_adapter import openai_live_adapter
from app.services.runtime_health import runtime_health_service
from app.services.task_service import task_service
from app.services.worker_coordinator import worker_coordinator

logging.basicConfig(
    level=logging.DEBUG if settings.debug else logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s — %(message)s",
)


@asynccontextmanager
async def lifespan(_: FastAPI):
    await ensure_database_ready()
    run_migrations()
    initialize_engine(settings.database_url)
    await conversation_store.mark_running_runs_interrupted()
    await task_service.repair_invalid_reminder_tasks()
    await worker_coordinator.reconcile_availability()
    await recover_pending_task_notifications()
    await jarvis_live_telemetry.start()
    await reminder_scheduler.start()
    await jarvis_coordinator.start()
    await runtime_health_service.start()
    try:
        yield
    finally:
        await runtime_health_service.stop()
        await jarvis_coordinator.stop()
        await openai_live_adapter.shutdown()
        await jarvis_live_telemetry.stop()
        await reminder_scheduler.stop()
        await run_supervisor.stop()
        await close_engine()

app = FastAPI(
    title="AI Cockpit",
    description="Personal AI command center",
    version="0.1.0",
    lifespan=lifespan,
)

# CORS — allow Next.js dev server + Tailscale host
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_origin_regex=settings.cors_origin_regex,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# API routers
app.include_router(auth.router)
app.include_router(chat.router)
app.include_router(conversations.router)
app.include_router(jarvis.router)
app.include_router(tasks.router)
app.include_router(knowledge.router)
app.include_router(apps.router)
app.include_router(notifications.router)
app.include_router(retrieval.router)
app.include_router(memory.router)
app.include_router(devices.router)


@app.get("/api/health")
async def health():
    return {"status": "ok", "version": "0.1.0"}


# Serve Next.js static export if it exists
_frontend_out = Path(__file__).parent.parent.parent / "frontend" / "out"
if _frontend_out.exists():
    app.mount("/", StaticFiles(directory=str(_frontend_out), html=True), name="frontend")
