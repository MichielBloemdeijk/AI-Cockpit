"""FastAPI application entry point."""
from __future__ import annotations

from contextlib import AsyncExitStack, asynccontextmanager
import asyncio
import logging
from pathlib import Path
from uuid import uuid4

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import JSONResponse

from app.config import settings
from app.api import app_platform, auth, chat, devices, jarvis, notifications, orchestration, task_context, tasks
from app.api.private import PRIVATE_HEADERS
from app.api import conversations, knowledge, worker_tools
from app.app_platform import app_runtime, close_apps_engine, ensure_apps_database, install_builtin_apps
from app.db.session import close_engine, ensure_database_ready, initialize_engine, run_migrations
from app.services.conversation_store import conversation_store
from app.services.run_supervisor import run_supervisor
from app.services.task_notifications import recover_pending_task_notifications
from app.services.reminder_scheduler import reminder_scheduler
from app.services.jarvis_coordinator import jarvis_coordinator
from app.services.jarvis_live_telemetry import jarvis_live_telemetry
from app.services.conversation_observer import conversation_observer
from app.services.openai_live_adapter import openai_live_adapter
from app.services.runtime_health import runtime_health_service
from app.services.task_service import task_service
from app.services.task_orchestration import task_orchestration_service
from app.services.task_context_upload import task_context_upload_service
from app.services.task_pool_supervisor import task_pool_manager
from app.services.worker_coordinator import worker_coordinator

logging.basicConfig(
    level=logging.DEBUG if settings.debug else logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s — %(message)s",
)
logger = logging.getLogger(__name__)


async def _expire_context_requests(stop: asyncio.Event) -> None:
    while not stop.is_set():
        try:
            await task_context_upload_service.expire_pending()
        except Exception:
            logger.exception("Unable to expire pending task context requests")
        try:
            await asyncio.wait_for(stop.wait(), timeout=2.0)
        except TimeoutError:
            pass


async def _stop_context_expiry(stop: asyncio.Event, task: asyncio.Task[None]) -> None:
    stop.set()
    await task


@asynccontextmanager
async def lifespan(application: FastAPI):
    from app.jarvis_tasks.acceptance import candidate_identity, database_identity
    application.state.acceptance_runtime = {
        "candidate": await asyncio.to_thread(candidate_identity),
        "database_identity": database_identity(settings.database_path),
        "instance_id": uuid4().hex,
    }
    # Each component registers its release as soon as it is acquired, so a
    # failure part-way through startup still stops what already started, in
    # reverse order.
    async with AsyncExitStack() as stack:
        stack.callback(setattr, application.state, "acceptance_runtime", None)
        await ensure_database_ready()
        run_migrations()
        initialize_engine(settings.database_url)
        stack.push_async_callback(close_engine)
        await conversation_store.mark_running_runs_interrupted()
        await task_service.repair_invalid_reminder_tasks()
        await worker_coordinator.reconcile_availability()
        await recover_pending_task_notifications()
        stack.push_async_callback(close_apps_engine)
        await ensure_apps_database()
        if settings.app_builtin_install:
            await install_builtin_apps()
        stack.push_async_callback(app_runtime.stop)
        if settings.app_runtime_enabled:
            from app.services.app_repair import start_app_repair
            await app_runtime.start(repair=start_app_repair)
        stack.push_async_callback(run_supervisor.stop)
        await jarvis_live_telemetry.start()
        stack.push_async_callback(jarvis_live_telemetry.stop)
        stack.push_async_callback(openai_live_adapter.shutdown)
        await reminder_scheduler.start()
        stack.push_async_callback(reminder_scheduler.stop)
        await jarvis_coordinator.start()
        stack.push_async_callback(jarvis_coordinator.stop)
        await conversation_observer.start()
        stack.push_async_callback(conversation_observer.stop)
        await runtime_health_service.start()
        stack.push_async_callback(runtime_health_service.stop)
        if settings.task_orchestration_enabled:
            await task_orchestration_service.start()
            stack.push_async_callback(task_orchestration_service.stop)
        if settings.task_pool_enabled:
            await task_pool_manager.start()
            stack.push_async_callback(task_pool_manager.stop)
        context_expiry_stop = asyncio.Event()
        context_expiry_task = asyncio.create_task(_expire_context_requests(context_expiry_stop))
        stack.push_async_callback(_stop_context_expiry, context_expiry_stop, context_expiry_task)
        yield

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
app.include_router(task_context.router)
app.include_router(tasks.router)
app.include_router(worker_tools.router)
app.include_router(orchestration.router)
app.include_router(knowledge.router)
app.include_router(app_platform.router)
app.include_router(app_platform.host_router)
app.include_router(notifications.router)
app.include_router(devices.router)


@app.get("/api/health")
async def health():
    return {"status": "ok", "version": "0.1.0"}


@app.middleware("http")
async def private_result_responses(request, call_next):
    # Cookie-authenticated writes and session creation must originate here.
    # Native bearer requests carry no browser cookies and need no Origin header.
    if request.method not in {"GET", "HEAD", "OPTIONS"}:
        origin = request.headers.get("origin")
        allowed = set(settings.cors_origins)
        from urllib.parse import urlparse
        if origin:
            try:
                parsed = urlparse(origin)
                if parsed.scheme in {"http", "https"} and parsed.hostname in {"127.0.0.1", "localhost"} and parsed.username is None:
                    allowed.add(origin)
            except ValueError:
                return JSONResponse({"detail": "Invalid request origin"}, status_code=403)
        if settings.jarvis_public_url:
            allowed.add(settings.jarvis_public_url.rstrip("/"))
        if (origin and origin not in allowed) or request.headers.get("sec-fetch-site") == "cross-site":
            return JSONResponse({"detail": "Cross-origin write rejected"}, status_code=403)
    response = await call_next(request)
    if request.url.path.startswith(("/api/app-platform", "/api/auth/", "/api/jarvis/devices")):
        for key, value in PRIVATE_HEADERS.items():
            response.headers[key] = value
    return response


# Serve Next.js static export if it exists
_result_export = Path(__file__).parent.parent.parent / "frontend" / "result-export" / "out"
_frontend_out = Path(__file__).parent.parent.parent / "frontend" / "out"


@app.get("/login", include_in_schema=False)
@app.get("/tasks", include_in_schema=False)
@app.get("/workspace/apps", include_in_schema=False)
async def focused_companion_shell(request: Request):
    from app.services.result_static import result_shell_response
    page = {"/login": "login", "/tasks": "tasks", "/workspace/apps": "apps"}[request.url.path]
    return result_shell_response(_result_export, page, fallback_root=_frontend_out)


@app.get("/tasks/{task_id}", include_in_schema=False)
async def focused_task_shell(task_id: str):
    from app.services.result_static import result_shell_response
    return result_shell_response(_result_export, "task")


if _result_export.exists():
    app.mount("/_result-assets", StaticFiles(directory=str(_result_export)), name="result-assets")

if _frontend_out.exists():
    app.mount("/", StaticFiles(directory=str(_frontend_out), html=True), name="frontend")
