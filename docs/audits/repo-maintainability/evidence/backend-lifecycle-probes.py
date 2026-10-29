"""Reproduce three audited control-flow defects without importing the application.

Run from the repository root with the backend Python interpreter. Source is
extracted with ast from the current checkout; collaborators, persistence and
provider operations are replaced with in-memory fakes. No application modules,
personal settings, databases, services or provider clients are initialized.

Exit zero means the *audited defects were reproduced*, not that application
behavior is correct. After repairs, replace these expectations with proper
behavioral regression tests in backend/tests rather than retaining this script
as an acceptance gate.
"""
from __future__ import annotations

import __future__
import ast
import asyncio
from contextlib import asynccontextmanager
import json
from pathlib import Path
import sys
from types import MethodType, ModuleType, SimpleNamespace


ROOT = Path(__file__).resolve().parents[4]
FLAGS = __future__.annotations.compiler_flag


def source_tree(relative: str) -> ast.Module:
    return ast.parse((ROOT / relative).read_text(encoding="utf-8-sig"))


def compile_nodes(nodes: list[ast.stmt], namespace: dict, label: str) -> None:
    exec(compile(ast.Module(body=nodes, type_ignores=[]), label, "exec", flags=FLAGS), namespace)


async def startup_cleanup_probe() -> dict:
    tree = source_tree("backend/app/main.py")
    lifespan = next(node for node in tree.body if isinstance(node, ast.AsyncFunctionDef) and node.name == "lifespan")
    trace = []

    def step(label, *, fail=False):
        async def call(*args, **kwargs):
            trace.append(label)
            if fail:
                raise RuntimeError("injected start failure")
        return call

    # The lifespan imports acceptance identity helpers locally. Supply a fake
    # module for that one import; restore sys.modules after the experiment.
    acceptance = ModuleType("app.jarvis_tasks.acceptance")
    acceptance.candidate_identity = lambda: {}
    acceptance.database_identity = lambda path: "isolated"
    previous = sys.modules.get(acceptance.__name__)
    sys.modules[acceptance.__name__] = acceptance
    try:
        namespace = {
            "asynccontextmanager": asynccontextmanager,
            "asyncio": asyncio,
            "uuid4": lambda: SimpleNamespace(hex="isolated"),
            "settings": SimpleNamespace(database_path=None, database_url="fake",
                task_orchestration_enabled=False, task_pool_enabled=False),
            "conversation_store": SimpleNamespace(mark_running_runs_interrupted=step("recover_runs")),
            "task_service": SimpleNamespace(repair_invalid_reminder_tasks=step("repair_reminders")),
            "worker_coordinator": SimpleNamespace(reconcile_availability=step("reconcile")),
            "openai_live_adapter": SimpleNamespace(shutdown=step("openai.shutdown")),
        }
        for name in ("ensure_database_ready", "close_engine", "recover_pending_task_notifications"):
            namespace[name] = step(name)
        for name in ("run_migrations", "initialize_engine"):
            namespace[name] = lambda *args: None
        for name in ("jarvis_live_telemetry", "reminder_scheduler", "jarvis_coordinator",
                     "conversation_observer", "runtime_health_service", "task_orchestration_service",
                     "task_pool_manager", "run_supervisor"):
            namespace[name] = SimpleNamespace(
                start=step(name + ".start", fail=name == "conversation_observer"),
                stop=step(name + ".stop"),
            )
        compile_nodes([lifespan], namespace, "<audit main.py lifespan>")
        error = None
        try:
            async with namespace["lifespan"](SimpleNamespace(state=SimpleNamespace())):
                raise AssertionError("Injected startup failure did not occur")
        except RuntimeError as exc:
            error = str(exc)
        cleanup = [item for item in trace if item.endswith(".stop") or item == "close_engine"]
        assert error == "injected start failure" and not cleanup
        assert "jarvis_coordinator.start" in trace
        return {"probe": "partial_startup_cleanup", "defect_reproduced": True,
                "error": error, "trace": trace, "cleanup_calls": cleanup}
    finally:
        if previous is None:
            sys.modules.pop(acceptance.__name__, None)
        else:
            sys.modules[acceptance.__name__] = previous


async def runner_restoration_probe() -> dict:
    run = SimpleNamespace(id="audit-run", status="queued", conversation_id="audit-conversation", metadata_json={})

    class Store:
        async def get_run(self, run_id):
            return run

        async def claim_run_for_execution(self, run_id):
            run.status = "running"
            return run

    @asynccontextmanager
    async def phase(**kwargs):
        yield

    async def broken_restore(**kwargs):
        raise RuntimeError("injected restoration failure")

    failures = []

    async def fail_run(*args, **kwargs):
        failures.append(args)

    runner_class = next(node for node in source_tree("backend/app/services/agent_runner.py").body
                        if isinstance(node, ast.ClassDef) and node.name == "AgentRunner")
    method = next(node for node in runner_class.body
                  if isinstance(node, ast.AsyncFunctionDef) and node.name == "continue_run")
    namespace = {"conversation_store": Store(), "_normalize_task_metadata": lambda data: data,
                 "_timed_agent_phase": phase, "restore_runtime_metadata_from_events": broken_restore,
                 "_fail_run": fail_run, "asyncio": asyncio}
    compile_nodes([method], namespace, "<audit agent_runner.py continue_run>")
    runner = SimpleNamespace(continue_run=MethodType(namespace["continue_run"], object()))
    supervisor_namespace = {}
    # This module's top-level imports are stdlib only. Its application imports
    # live in unused methods/branches, so executing it starts no application.
    exec(compile((ROOT / "backend/app/services/run_supervisor.py").read_text(encoding="utf-8-sig"),
                 "<audit run_supervisor.py>", "exec"), supervisor_namespace)
    logs = []
    supervisor_namespace["logger"] = SimpleNamespace(exception=lambda *args, **kwargs: logs.append(args))
    supervisor = supervisor_namespace["RunSupervisor"]()
    task = supervisor.start(run.id, runner=runner)
    await asyncio.gather(task, return_exceptions=True)
    await asyncio.sleep(0)
    assert isinstance(task.exception(), RuntimeError)
    assert run.status == "running" and not failures and not logs and supervisor.get(run.id) is None
    return {"probe": "runner_restoration_failure", "defect_reproduced": True,
            "exception": str(task.exception()), "durable_status": run.status,
            "failure_transition_calls": len(failures), "supervisor_error_logs": len(logs),
            "supervisor_still_owns_run": supervisor.get(run.id) is not None}


async def revocation_action_probe() -> dict:
    tree = source_tree("backend/app/services/task_service.py")
    actions = next(node for node in tree.body if isinstance(node, ast.Assign)
                   and any(isinstance(target, ast.Name) and target.id == "CONTROL_ACTIONS" for target in node.targets))
    service = next(node for node in tree.body if isinstance(node, ast.ClassDef) and node.name == "TaskService")
    control = next(node for node in service.body if isinstance(node, ast.AsyncFunctionDef) and node.name == "control")
    runtime = source_tree("backend/app/services/codex_task_runtime.py")
    call = next(node for node in ast.walk(runtime) if isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute) and node.func.attr == "control"
                and any(keyword.arg == "requester" and isinstance(keyword.value, ast.Constant)
                        and keyword.value.value == "worker_policy" for keyword in node.keywords))
    action = next(keyword.value.value for keyword in call.keywords if keyword.arg == "action")
    namespace = {}
    compile_nodes([actions, control], namespace, "<audit task_service.py control>")
    error = None
    try:
        await namespace["control"](None, "isolated-task", action=action, idempotency_key="isolated")
    except ValueError as exc:
        error = str(exc)
    assert action not in namespace["CONTROL_ACTIONS"]
    assert error == f"Unsupported task control action: {action}"
    return {"probe": "policy_revocation_action", "defect_reproduced": True,
            "runtime_action": action, "runtime_call_line": call.lineno,
            "supported_actions": sorted(namespace["CONTROL_ACTIONS"]), "error": error}


async def main() -> None:
    for probe in (startup_cleanup_probe, runner_restoration_probe, revocation_action_probe):
        print(json.dumps(await probe(), sort_keys=True))


if __name__ == "__main__":
    asyncio.run(main())
