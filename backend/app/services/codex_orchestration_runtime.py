"""Async supervisor for the restricted, separate background Codex host."""
from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
import socket
import sys

from sqlalchemy import update

from app.config import settings
from app.db.repositories.tasks import TaskRepository
from app.db.session import session_scope
from app.db.tables import OrchestrationDecision
from app.services.task_orchestration import DecisionClaim, DecisionTools, RuntimeUnavailable
from app.services.windows_job import process_creation_stamp
from app.services.orchestration_telemetry import record, token_usage


class CodexOrchestrationRuntime:
    def __init__(self, *, permitted: bool = False) -> None:
        self.permitted = permitted
        self.process: asyncio.subprocess.Process | None = None
        self.native_started = asyncio.Event()
        self.auth_owner: str | None = None

    async def decide(self, claim: DecisionClaim, tools: DecisionTools) -> dict:
        if not self.permitted:
            raise RuntimeUnavailable("Unattended live orchestration is not enabled")
        if self.process is not None:
            raise RuntimeError("An orchestration host is already running")
        async with session_scope() as session:
            row = await session.get(OrchestrationDecision, claim.id)
            model, effort, profile = row.model, row.effort, row.auth_profile
        flags = getattr(__import__("subprocess"), "CREATE_NO_WINDOW", 0) if os.name == "nt" else 0
        process = await asyncio.create_subprocess_exec(
            sys.executable, "-m", "app.jarvis_tasks.orchestration_host",
            stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL, creationflags=flags,
            limit=1024 * 1024,
        )
        self.process = process
        self.native_started.clear()
        binding = {"host_pid": process.pid, "host_creation_stamp": process_creation_stamp(process.pid),
                   "host_hostname": socket.gethostname(),
                   "runtime_version": "0.155.1"}
        payload = {"decision_id": claim.id, "generation": claim.generation, "owner": claim.owner,
                   "parent_pid": os.getpid(), "database_url": settings.resolved_database_url,
                   "profile": str(Path(profile).resolve()), "model": model, "effort": effort,
                   "workspace": str((Path(settings.task_orchestration_context_dir) / claim.id).resolve()),
                   "context": claim.context}
        connected, outcome, sequence = False, None, 0
        try:
            from app.services.runtime_auth_routes import select_route, task_subscription_denial
            self.auth_owner = f"{claim.id}:{claim.generation}"
            auth = await select_route(owner_kind="background", owner_id=self.auth_owner,
                pid=process.pid, stamp=process_creation_stamp(process.pid) or 0,
                contained=os.name == "nt", api_profile=Path(profile).resolve(),
                unavailable_reason=await task_subscription_denial(claim.context["task"]["id"]))
            payload["profile"], payload["auth_route"] = str(auth.profile), auth.route
            binding["auth_route"] = auth.route
            binding["auth_profile"] = str(auth.profile)
            async with session_scope() as session:
                await record(session, key=f"auth:{claim.id}:{claim.generation}:{auth.route}", kind="auth_route",
                    decision_id=claim.id, generation=claim.generation, task_id=claim.context["task"]["id"],
                    values={"route": auth.route, "reason": auth.reason})
            # Persist host identity before it can start a native turn. Even a
            # crash before thread/turn acknowledgment must remain uncertain.
            async with session_scope() as session:
                result = await session.execute(update(OrchestrationDecision).where(
                    OrchestrationDecision.id == claim.id, OrchestrationDecision.status == "running",
                    OrchestrationDecision.claim_generation == claim.generation,
                    OrchestrationDecision.claim_owner == claim.owner,
                ).values(native_binding_json=dict(binding)))
                if result.rowcount != 1:
                    raise RuntimeError("Native host lost its decision generation")
            process.stdin.write((json.dumps(payload, ensure_ascii=True) + "\n").encode())
            await process.stdin.drain()
            while True:
                line = await asyncio.wait_for(process.stdout.readline(), 120.0 if connected else 10.0)
                if not line:
                    break
                message = json.loads(line)
                kind = message.get("type")
                if kind == "auth_unavailable":
                    from app.services.runtime_auth_routes import profile_revision
                    async with session_scope() as session:
                        await TaskRepository(session).append_event(task_id=claim.context["task"]["id"],
                            event_type="task.auth.unavailable", payload_json={"reason": message["reason"],
                                "profile_revision": profile_revision(), "next_boundary_only": True,
                                "automatic_replay": False},
                            idempotency_key=f"orchestration-auth-unavailable:{claim.id}:{claim.generation}")
                    # The current host drains/exits first. Recovery then proves
                    # the native boundary or its before-turn state; it never
                    # substitutes credentials inside an active native turn.
                    if message.get("before_turn") and not binding.get("turn_id"):
                        outcome = {"type": "result", "status": "auth_unavailable", "before_turn": True}
                        binding["before_turn_auth_unavailable"] = True
                        async with session_scope() as session:
                            await session.execute(update(OrchestrationDecision).where(OrchestrationDecision.id == claim.id,
                                OrchestrationDecision.claim_generation == claim.generation,
                                OrchestrationDecision.claim_owner == claim.owner).values(native_binding_json=dict(binding)))
                    continue
                if kind == "error":
                    raise RuntimeError(message.get("detail") or "Codex orchestration host failed")
                if kind in {"connected", "binding"}:
                    connected = True
                    binding.update({key: message[key] for key in ("thread_id", "turn_id", "tools") if key in message})
                    async with session_scope() as session:
                        result = await session.execute(update(OrchestrationDecision).where(
                            OrchestrationDecision.id == claim.id, OrchestrationDecision.status == "running",
                            OrchestrationDecision.claim_generation == claim.generation,
                            OrchestrationDecision.claim_owner == claim.owner,
                        ).values(native_binding_json=dict(binding)))
                        if result.rowcount != 1:
                            raise RuntimeError("Native host lost its decision generation")
                        if kind == "binding":
                            await record(session, key=f"native:{claim.id}:{claim.generation}", kind="native_turn",
                                decision_id=claim.id, generation=claim.generation, task_id=claim.context["task"]["id"],
                                values={"thread_id": binding["thread_id"], "turn_id": binding["turn_id"],
                                        "model": model, "effort": effort})
                    if kind == "binding":
                        self.native_started.set()
                elif kind == "event":
                    sequence += 1
                    async with session_scope() as session:
                        await TaskRepository(session).append_event(
                            task_id=claim.context["task"]["id"], event_type="task.orchestration.runtime",
                            payload_json={"decision_id": claim.id, "native_event": message},
                            idempotency_key=f"orchestration-audit:{claim.id}:{claim.generation}:native:{sequence}",
                        )
                        if message.get("event_type") == "thread/tokenUsage/updated" and binding.get("turn_id"):
                            usage = token_usage(message.get("payload") or {}, thread_id=binding["thread_id"], turn_id=binding["turn_id"])
                            if usage:
                                await record(session, key=f"usage:{claim.id}:{claim.generation}", kind="native_usage",
                                    decision_id=claim.id, generation=claim.generation, task_id=claim.context["task"]["id"],
                                    values=usage, replace=True)
                elif kind == "result":
                    outcome = message
            await process.wait()
            if process.returncode != 0 or outcome is None:
                raise RuntimeError("Native Codex turn outcome is uncertain; inspect persisted binding before retry")
            if outcome.get("status") == "auth_unavailable":
                # The host confirmed no thread/turn started and has exited.
                # One API fallback uses the same decision and durable task.
                await self.stop()
                return await self.decide(claim, tools)
            return outcome
        finally:
            await self.stop()

    async def inspect(self, binding: dict, profile: str) -> dict:
        """Read stored history in a contained host; never start/resume a turn."""
        flags = getattr(__import__("subprocess"), "CREATE_NO_WINDOW", 0) if os.name == "nt" else 0
        process = await asyncio.create_subprocess_exec(
            sys.executable, "-m", "app.jarvis_tasks.orchestration_host",
            stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL, creationflags=flags, limit=1024 * 1024,
        )
        selected_profile = Path(binding.get("auth_profile") or profile).resolve()
        payload = {"mode": "inspect", "parent_pid": os.getpid(), "profile": str(selected_profile),
                   "thread_id": binding["thread_id"], "turn_id": binding.get("turn_id")}
        inspection_owner = None
        try:
            from app.services.runtime_auth_routes import subscription_profile, select_route
            if selected_profile == subscription_profile():
                inspection_owner = f"inspection:{process.pid}:{binding['thread_id']}"
                api_profile = Path(settings.task_orchestration_profile)
                api_profile = (api_profile if api_profile.is_absolute() else settings.backend_root / api_profile).resolve()
                auth = await select_route(owner_kind="background", owner_id=inspection_owner,
                    pid=process.pid, stamp=process_creation_stamp(process.pid) or 0,
                    api_profile=api_profile, contained=os.name == "nt")
                if auth.route != "subscription":
                    raise RuntimeUnavailable("Subscription native inspection waits for the credential owner to stop")
            stdout, _ = await asyncio.wait_for(process.communicate((json.dumps(payload) + "\n").encode()), 15.0)
            message = json.loads(stdout)
            if process.returncode != 0 or message.get("type") != "inspection":
                raise RuntimeUnavailable("Read-only native inspection failed")
            return message
        finally:
            if process.returncode is None:
                process.kill()
            await process.wait()
            if inspection_owner:
                from app.services.runtime_auth_routes import release_stopped_owner
                await release_stopped_owner(owner_kind="background", owner_id=inspection_owner, pid=process.pid)

    async def stop(self) -> None:
        process = self.process
        if process is None:
            return
        if process.returncode is None:
            # Exact owned host only. Its Windows job closes all descendants.
            process.kill()
        await process.wait()
        if self.auth_owner is not None:
            from app.services.runtime_auth_routes import release_stopped_owner
            await release_stopped_owner(owner_kind="background", owner_id=self.auth_owner, pid=process.pid)
            self.auth_owner = None
        self.process = None
