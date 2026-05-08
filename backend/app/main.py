"""FastAPI application entry point."""
from __future__ import annotations

from contextlib import asynccontextmanager
import logging
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from app.config import settings
from app.api import apps, auth, chat
from app.api import conversations, knowledge
from app.db.session import close_engine, ensure_database_ready, initialize_engine, run_migrations
from app.services.conversation_store import conversation_store

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
    try:
        yield
    finally:
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
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# API routers
app.include_router(auth.router)
app.include_router(chat.router)
app.include_router(conversations.router)
app.include_router(knowledge.router)
app.include_router(apps.router)


@app.get("/api/health")
async def health():
    return {"status": "ok", "version": "0.1.0"}


# Serve Next.js static export if it exists
_frontend_out = Path(__file__).parent.parent.parent / "frontend" / "out"
if _frontend_out.exists():
    app.mount("/", StaticFiles(directory=str(_frontend_out), html=True), name="frontend")
