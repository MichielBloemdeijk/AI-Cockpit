"""Task-scoped durable lifecycle and frozen-context tools for native workers."""
from __future__ import annotations

import argparse
import asyncio
import base64
from hashlib import sha256
import json
from pathlib import Path
import sys
from typing import Any

from sqlalchemy import select


# App Server starts this script from the task workspace, not the backend root.
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from app.db.session import close_engine, initialize_engine, session_scope  # noqa: E402
from app.db.tables import TaskArtifact, TaskContextBlob, TaskContextRequest, TaskEvent, TaskExecutionRun, TaskTurn  # noqa: E402
from app.config import settings  # noqa: E402
from app.services.tasks.service import task_service  # noqa: E402
from app.services.tasks.workflows import validate_pinned_workflow  # noqa: E402
from app.services.tasks.capabilities import workflow_tools  # noqa: E402
from app.services.results.types import publish_schema  # noqa: E402

# A build_app run keeps the package it builds here, inside its workspace.
APP_PACKAGE_DIR = "app"
APP_CHECK_DIR = ".app-check"


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()
    parser.add_argument("--database-url", required=True)
    parser.add_argument("--task-id", required=True)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--attempt-id", required=True)
    parser.add_argument("--claim-token", required=True)
    parser.add_argument("--workspace", type=Path, required=True)
    parser.add_argument("--workflow-schema")
    parser.add_argument("--workflow-capabilities", default="")
    parser.add_argument("--answer-only", action="store_true")
    parser.add_argument("--workflow-allowlist", default="")
    parser.add_argument("--context-blobs-dir", default="")
    parser.add_argument("--project-roots", default="")
    parser.add_argument("--local-broker", action="store_true")
    parser.add_argument("--apps-database-url", default="")
    parser.add_argument("--apps-dir", default="")
    return parser


def _result(payload: dict[str, Any], *, error: bool = False) -> dict[str, Any]:
    return {
        "content": [{"type": "text", "text": json.dumps(payload, ensure_ascii=True)}],
        "structuredContent": payload,
        "isError": error,
    }


def _terminal_tools() -> list[dict[str, Any]]:
    return [
    {"name": "task_question", "description": "Ask one material clarification and checkpoint this run. Stop tools after success.",
     "inputSchema": {"type": "object", "properties": {"question": {"type": "string"}, "operation_key": {"type": "string"}},
                     "required": ["question", "operation_key"], "additionalProperties": False}},
    {"name": "task_finish", "description": "Answer a question without revising a result, or report failure. Stop tools after success.",
     "inputSchema": {"type": "object", "properties": {"summary": {"type": "string"}, "status": {"type": "string", "enum": ["completed", "failed"]}, "operation_key": {"type": "string"}},
                     "required": ["summary", "status", "operation_key"], "additionalProperties": False}},
    ]


def _app_package_tools() -> list[dict[str, Any]]:
    return [
        {"name": "app_checkout", "description": (
            "Copy an installed app's current package into ./app so you can change or fix it. Returns its "
            "manifest summary, the version to set next, and recent errors the app reported. New apps skip this "
            "and create ./app themselves."),
         "inputSchema": {"type": "object", "properties": {"app": {"type": "string", "description": "Slug or title"}},
                         "required": ["app"], "additionalProperties": False}},
        {"name": "app_catalog", "description": (
            "The data other apps hold, for an app that reads or writes it through grants: every installed app "
            "with each collection's JSON Schema, record count and one example record, plus the read-only system "
            "collections (system.tasks, system.results, system.conversations)."),
         "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False}},
        {"name": "app_check", "description": (
            "Check ./app the way installation will: manifest, compatibility with the installed version and "
            "stored data, and the UI in headless Chromium under the real sandbox, running tests/ui.json. "
            "Returns problems, each test's outcome, the rendered text and screenshot paths under "
            "./.app-check. Fix every problem before result_publish."),
         "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False}},
    ]


async def _tool_catalog(workflow_schema: str | None = None, capabilities: list[str] | None = None, answer_only: bool = False, local_broker: bool = False) -> list[dict[str, Any]]:
    common = [
        {
            "name": "task_history",
            "description": (
                "Read this task's durable events and completed native_research references. "
                "native_research provides source_event_sequence without paging streaming notifications. "
                "For a large event, "
                "set event_sequence, turn_sequence, or artifact_id plus byte_offset to retrieve "
                "original evidence in chunks. "
                "Use this to verify compact handoff context; it cannot read another task."
            ),
            "inputSchema": {
                "type": "object",
                "properties": {
                    "after_sequence": {"type": "integer", "minimum": 0},
                    "event_sequence": {"type": "integer", "minimum": 1},
                    "turn_sequence": {"type": "integer", "minimum": 1},
                    "artifact_id": {"type": "string"},
                    "byte_offset": {"type": "integer", "minimum": 0},
                },
                "additionalProperties": False,
            },
        },
    ]
    if workflow_schema:
        selected = set(workflow_tools({"capabilities": capabilities})) if capabilities is not None else {
            "task_history", "result_publish"}
        if "task_context_images" in selected:
            common.append({
                "name": "task_context_images",
                "description": (
                    "Return the complete validated screen collection bound to this task as image content. "
                    "One call supplies every distinct image, labeled with invocation/latest roles, "
                    "capture times and provenance, plus any missing optional roles. "
                    "Read the visible subject directly from these images; the user's screen-based "
                    "request need not repeat it in text. Repeating this call returns the same frozen collection."
                ),
                "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False},
            })
        schema = await publish_schema(workflow_schema)
        properties = schema["properties"] | {"operation_key": {"type": "string"},
            "base_revision": {"type": "integer", "minimum": 0, "description": "Expected current revision from task_history; required when updating an existing result."}}
        tools = common + _app_package_tools() + [{
            "name": "result_publish",
            "description": ("Check and install ./app, then publish what you built and complete this run. If the "
                            "check fails nothing is installed and the problems are returned."
                            if workflow_schema == "app-build.v1" else
                            "Atomically publish a typed workflow result and complete this run. A result kept "
                            "in an app is saved as that app's record; when revising it, publish the complete "
                            "new content with base_revision from task_history's maintained_result."),
            "inputSchema": {
                "type": "object",
                "properties": properties,
                "required": (schema.get("required") or []) + ["operation_key"],
                "$defs": schema.get("$defs", {}),
                "additionalProperties": False,
            },
        }]
        tools.extend(_terminal_tools())
        common = [tool for tool in tools if tool["name"] in selected and not (answer_only and tool["name"] == "result_publish")]
    if not workflow_schema:
        common += _terminal_tools()
    if local_broker:
        from app.services.tasks.project_tools import project_tool_catalog
        common += project_tool_catalog()
    return common


async def _image_result(arguments: dict[str, Any], args: argparse.Namespace) -> dict[str, Any]:
    if not getattr(args, "workflow_schema", None) or arguments:
        raise ValueError("Image inspection is unavailable for this workflow or arguments")
    await task_service.validate_execution_authority(
        args.task_id, attempt_id=args.attempt_id,
        execution_run_id=args.run_id, claim_token=args.claim_token,
    )
    async with session_scope() as session:
        run = await session.get(TaskExecutionRun, args.run_id)
        if (run is None or run.task_id != args.task_id
                or "task.context.images@2" not in validate_pinned_workflow(dict(run.configuration_json or {}))["capabilities"]):
            raise ValueError("This run is not an image-capable workflow")
        context_run_id = await session.scalar(select(TaskContextRequest.execution_run_id).join(
            TaskExecutionRun, TaskExecutionRun.id == TaskContextRequest.execution_run_id).where(
                TaskContextRequest.task_id == args.task_id, TaskExecutionRun.ordinal <= run.ordinal
            ).order_by(TaskExecutionRun.ordinal.desc()).limit(1))
        requests = list((await session.execute(select(TaskContextRequest).where(
            TaskContextRequest.execution_run_id == context_run_id).order_by(TaskContextRequest.capture_id))).scalars().all())
        if not requests or any(request.state != "ready" or request.blob_id is None for request in requests):
            raise ValueError("This run has no complete validated screen collection")
        context_run = await session.get(TaskExecutionRun, context_run_id)
        manifest = (context_run.configuration_json or {}).get("screen_context", {})
        content, images, seen = [], [], {}
        for request in requests:
            blob = await session.get(TaskContextBlob, request.blob_id)
            if blob is None or blob.mime_type != "image/jpeg" or blob.byte_length > 5 * 1024 * 1024:
                raise ValueError("Validated screen context is unavailable")
            storage_root = settings.task_context_blobs_path
            path = Path(blob.storage_path)
            if path.is_symlink() or not path.resolve().is_relative_to(storage_root):
                raise ValueError("Screen context storage is outside the private root")
            data = path.read_bytes()
            if len(data) != blob.byte_length or sha256(data).hexdigest() != blob.sha256:
                raise ValueError("Screen context checksum is invalid")
            roles = [image for image in manifest.get("images", []) if image["capture_id"] == request.capture_id]
            if blob.sha256 not in seen:
                seen[blob.sha256] = len(seen)
                content.extend([{"type": "text", "text": json.dumps({"image_index": seen[blob.sha256],
                    "roles": roles, "instruction": "Select relevant context using the user request. Do not silently merge conflicting screen contents."})},
                    {"type": "image", "data": base64.b64encode(data).decode("ascii"), "mimeType": "image/jpeg"}])
            images.append({"roles": roles, "image_index": seen[blob.sha256],
                "context_reference": f"context://{blob.id}", "sha256": blob.sha256,
                "width": blob.width, "height": blob.height, "capture_id": request.capture_id})
        content.append({"type": "text", "text": json.dumps({"images": images, "missing_roles": manifest.get("missing_roles", [])})})
        return {"content": content, "structuredContent": {"schema": "task-screen-context.v2",
            "images": images, "missing_roles": manifest.get("missing_roles", [])}, "isError": False}



async def _call(name: str, arguments: dict[str, Any], args: argparse.Namespace) -> dict[str, Any]:
    workflow_schema = getattr(args, "workflow_schema", None)
    allowed = set()
    if workflow_schema:
        async with session_scope() as session:
            run = await session.get(TaskExecutionRun, args.run_id)
            if run is None or run.task_id != args.task_id:
                raise ValueError("Workflow run does not belong to this task")
            profile = validate_pinned_workflow(dict(run.configuration_json or {}))
            if profile["output_schema"] != workflow_schema:
                raise ValueError("Workflow schema does not match this run")
            allowed = set(workflow_tools(profile))
            if (run.configuration_json or {}).get("result", {}).get("mode") == "answer":
                allowed.discard("result_publish")
    if workflow_schema and name not in allowed:
        if not (name.startswith("project_") and getattr(args, "local_broker", False)):
            raise ValueError("Tool is not allowlisted for this workflow")
    if name.startswith("project_"):
        if not getattr(args, "local_broker", False):
            raise ValueError("Tool is not allowlisted")
        async with session_scope() as session:
            run = await session.get(TaskExecutionRun, args.run_id)
            resolved = (run.configuration_json or {}).get("resolved_tools", {}) if run else {}
            if (run is None or run.task_id != args.task_id or "native.local" not in resolved.get("tools", [])
                    or resolved.get("local_execution_mode") != "trusted"):
                raise ValueError("This run did not select trusted local execution")
        from app.services.tasks.project_tools import call_project_tool
        from app.services.tasks.worker_tool_policy import read_policy
        _, policy = await read_policy()
        if "native.local" in policy["disabled"]:
            raise ValueError("Local tool access was revoked")
        args.command_mode = "trusted"
        return await call_project_tool(name, arguments, args)
    if name in {"task_question", "task_finish"}:
        expected = {"question", "operation_key"} if name == "task_question" else {"summary", "status", "operation_key"}
        if set(arguments) != expected or any(not isinstance(value, str) or not value.strip() for value in arguments.values()):
            raise ValueError("Question/finish arguments are invalid")
        key = arguments["operation_key"]
        if len(key) > 100:
            raise ValueError("Operation key is too long")
        fence = dict(attempt_id=args.attempt_id, execution_run_id=args.run_id, claim_token=args.claim_token)
        if name == "task_question":
            question = await task_service.ask_question(args.task_id, question=arguments["question"],
                idempotency_key=f"workflow:{args.run_id}:{key}", **fence)
            return {"status": "waiting_for_input", "question_id": question.id}
        if not workflow_schema and arguments["status"] == "completed":
            async with session_scope() as session:
                run = await session.get(TaskExecutionRun, args.run_id)
                intent = (run.configuration_json or {}).get("result") if run else None
                if intent and intent.get("mode") != "answer":
                    raise ValueError("Return the complete document in the final assistant message for publication")
        await task_service.complete(args.task_id, summary=arguments["summary"], status=arguments["status"],
            final_text=arguments["summary"] if arguments["status"] == "completed" else None,
            workflow_answer=True, idempotency_key=f"workflow:{args.run_id}:{key}", **fence)
        return {"status": arguments["status"]}
    if name in {"app_checkout", "app_check", "app_catalog"}:
        await task_service.validate_execution_authority(args.task_id, attempt_id=args.attempt_id,
            execution_run_id=args.run_id, claim_token=args.claim_token)
        return await _app_package_call(name, arguments, args)
    if name == "result_publish":
        if workflow_schema is None:
            raise ValueError("Result publication is not allowlisted for this profile")
        if "operation_key" not in arguments:
            raise ValueError("Result publication has missing or unsupported arguments")
        content = {field: value for field, value in arguments.items() if field not in {"operation_key", "base_revision"}}
        key = arguments["operation_key"]
        if workflow_schema == "app-build.v1":
            try:
                await task_service.validate_execution_authority(args.task_id, attempt_id=args.attempt_id,
                    execution_run_id=args.run_id, claim_token=args.claim_token)
            except ValueError:
                # Without authority nothing is installed; only replaying the
                # completed publication can still succeed below.
                content.update(_package_identity(args))
            else:
                installed = await _install_built_app(args)
                if "problems" in installed:
                    return installed
                content.update(installed)
        await task_service.complete(
            args.task_id, summary=content["title"], idempotency_key=f"workflow:{args.run_id}:{key}",
            workflow_result=content, workflow_operation_key=key,
            workflow_base_revision=arguments.get("base_revision"),
            attempt_id=args.attempt_id, execution_run_id=args.run_id,
            claim_token=args.claim_token,
        )
        return {"status": "completed", "schema": workflow_schema, "title": content["title"]}
    if name == "task_context_images":
        raise ValueError("Image content is available only as the typed MCP image response")
    if name == "task_history":
        for field in ("after_sequence", "event_sequence", "turn_sequence", "byte_offset"):
            value = arguments.get(field, 0)
            if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                raise ValueError(f"{field} must be a nonnegative integer")
        selectors = sum(field in arguments for field in ("event_sequence", "turn_sequence", "artifact_id"))
        if selectors > 1:
            raise ValueError("Select one original evidence item at a time")
        await task_service.validate_execution_authority(
            args.task_id,
            attempt_id=args.attempt_id,
            execution_run_id=args.run_id,
            claim_token=args.claim_token,
        )
        if "turn_sequence" in arguments or "artifact_id" in arguments:
            async with session_scope() as session:
                if "turn_sequence" in arguments:
                    turn = (await session.execute(select(TaskTurn).where(
                        TaskTurn.task_id == args.task_id,
                        TaskTurn.sequence == arguments["turn_sequence"],
                    ))).scalar_one_or_none()
                    if turn is None:
                        raise ValueError("Turn does not belong to this task")
                    evidence = {"kind": "turn", "sequence": turn.sequence,
                                "role": turn.role, "content": turn.content,
                                "artifact_reference": turn.artifact_reference}
                else:
                    artifact_id = arguments["artifact_id"]
                    if not isinstance(artifact_id, str) or not artifact_id:
                        raise ValueError("artifact_id must be a non-empty string")
                    artifact = (await session.execute(select(TaskArtifact).where(
                        TaskArtifact.task_id == args.task_id,
                        TaskArtifact.id == artifact_id,
                    ))).scalar_one_or_none()
                    if artifact is None:
                        raise ValueError("Artifact does not belong to this task")
                    evidence = {"kind": "artifact", "id": artifact.id,
                                "type": artifact.artifact_type,
                                "content_text": artifact.content_text,
                                "content_json": artifact.content_json,
                                "uri": artifact.uri}
            encoded = json.dumps(evidence, ensure_ascii=True, separators=(",", ":")).encode("ascii")
            offset = arguments.get("byte_offset", 0)
            if offset > len(encoded):
                raise ValueError("byte_offset exceeds evidence payload")
            end = min(offset + 48_000, len(encoded))
            return {"evidence_json_chunk": encoded[offset:end].decode("ascii"),
                    "byte_offset": offset, "next_byte_offset": end,
                    "total_bytes": len(encoded), "sha256": sha256(encoded).hexdigest()}
        if "event_sequence" in arguments:
            sequence = arguments["event_sequence"]
            if sequence < 1:
                raise ValueError("event_sequence must be positive")
            async with session_scope() as session:
                event = (await session.execute(select(TaskEvent).where(
                    TaskEvent.task_id == args.task_id, TaskEvent.sequence == sequence
                ))).scalar_one_or_none()
            if event is None:
                raise ValueError("Event does not belong to this task")
            encoded = json.dumps(event.payload_json, ensure_ascii=True, separators=(",", ":")).encode("ascii")
            offset = arguments.get("byte_offset", 0)
            if offset > len(encoded):
                raise ValueError("byte_offset exceeds event payload")
            end = min(offset + 48_000, len(encoded))
            return {
                "sequence": sequence, "payload_json_chunk": encoded[offset:end].decode("ascii"),
                "byte_offset": offset, "next_byte_offset": end,
                "total_bytes": len(encoded), "sha256": sha256(encoded).hexdigest(),
            }
        cursor = arguments.get("after_sequence", 0)
        async with session_scope() as session:
            from sqlalchemy import func
            latest = int((await session.execute(select(func.max(TaskEvent.sequence)).where(
                TaskEvent.task_id == args.task_id))).scalar_one_or_none() or 0)
            events = list((await session.execute(select(TaskEvent).where(TaskEvent.task_id == args.task_id,
                TaskEvent.sequence > cursor, TaskEvent.event_type != "agent.native.event")
                .order_by(TaskEvent.sequence).limit(20))).scalars().all())
            native_research = list((await session.execute(select(TaskEvent).where(TaskEvent.task_id == args.task_id,
                TaskEvent.event_type.in_(["agent.native.item", "agent.native.subagent.item"]),
                TaskEvent.payload_json["item"]["type"].as_string() == "webSearch")
                .order_by(TaskEvent.sequence.desc()).limit(20))).scalars().all())
            from app.services.native_research import is_research_event
            native_research = [{"source_event_sequence": event.sequence, "run_id": event.execution_run_id,
                "item": event.payload_json["item"]} for event in native_research if is_research_event(event)][:20]
        result: list[dict[str, Any]] = []
        used = 0
        for event in events:
            payload = event.payload_json
            encoded = json.dumps(payload, ensure_ascii=False)
            size = len(encoded.encode("utf-8"))
            if result and used + size > 128_000:
                break
            result.append({
                "sequence": event.sequence,
                "run_id": event.execution_run_id,
                "type": event.event_type,
                "payload": payload if size <= 128_000 else None,
                "payload_oversize": size > 128_000,
            })
            used += min(size, 128_000)
        next_cursor = result[-1]["sequence"] if result and (len(events) == 20 or len(result) < len(events)) else latest
        from app.services.results.contract import context_result, maintained_view
        async with session_scope() as session:
            maintained_result = await context_result(session, args.task_id, args.run_id)
        maintained = await maintained_view(maintained_result, content_limit=128_000)
        return {"events": result, "native_research": native_research, "maintained_result": maintained, "next_cursor": next_cursor,
                "latest_sequence": latest, "has_more": next_cursor < latest}
    raise ValueError("Tool is not allowlisted")


async def _app_package_call(name: str, arguments: dict[str, Any], args: argparse.Namespace) -> dict[str, Any]:
    from app import app_platform

    package = args.workspace / APP_PACKAGE_DIR
    if name == "app_check":
        return await app_platform.check_package(package, screenshots=args.workspace / APP_CHECK_DIR)
    if name == "app_catalog":
        if arguments:
            raise ValueError("app_catalog takes no arguments")
        return await app_platform.data_catalog()
    reference = arguments.get("app")
    if set(arguments) != {"app"} or not isinstance(reference, str) or not reference.strip():
        raise ValueError("app_checkout needs the app's slug or title")
    try:
        app = await app_platform.find_app(reference)
    except app_platform.AppNotFound:
        return {"found": False, "apps": [item.slug for item in await app_platform.list_apps()]}
    if not (package / "cockpit-app.json").is_file():
        await app_platform.checkout(app.slug, package)
    status = await app_platform.status(app.slug)
    return {"found": True, "path": APP_PACKAGE_DIR, **status, "next_version": app.version + 1}


def _package_identity(args: argparse.Namespace) -> dict[str, Any]:
    try:
        manifest = json.loads((args.workspace / APP_PACKAGE_DIR / "cockpit-app.json").read_text(encoding="utf-8"))
        return {"slug": manifest["slug"], "version": manifest["version"]}
    except (OSError, ValueError, KeyError, TypeError) as exc:
        raise ValueError("The app package has no readable manifest") from exc


async def _install_built_app(args: argparse.Namespace) -> dict[str, Any]:
    """Install ./app when it passes the check; returns the installed identity or the problems."""
    from app import app_platform

    package = args.workspace / APP_PACKAGE_DIR
    report = await app_platform.check_package(package, screenshots=args.workspace / APP_CHECK_DIR)
    if not report["ok"]:
        return {"status": "not_installed", "problems": report["problems"],
                "tests": [{key: test[key] for key in ("name", "ok", "error", "problems")} for test in report["tests"]]}
    try:
        installed = await app_platform.install_package(package)
    except app_platform.ManifestError as exc:
        return {"status": "not_installed", "problems": [str(exc)], "tests": []}
    return {"slug": installed.slug, "version": installed.version}


async def _handle(request: dict[str, Any], args: argparse.Namespace) -> dict[str, Any] | None:
    request_id = request.get("id")
    if request_id is None:
        return None
    method = request.get("method")
    if method == "initialize":
        version = (request.get("params") or {}).get("protocolVersion") or "2024-11-05"
        result = {
            "protocolVersion": version,
            "capabilities": {"tools": {"listChanged": False}},
            "serverInfo": {"name": "jarvis-task", "version": "1"},
        }
    elif method == "tools/list":
        capabilities = json.loads(args.workflow_capabilities) if getattr(args, "workflow_capabilities", "") else None
        result = {"tools": await _tool_catalog(getattr(args, "workflow_schema", None), capabilities, getattr(args, "answer_only", False), getattr(args, "local_broker", False))}
    elif method == "tools/call":
        params = request.get("params") or {}
        name = str(params.get("name") or "")
        arguments = params.get("arguments") or {}
        try:
            if not isinstance(arguments, dict):
                raise ValueError("Tool arguments must be an object")
            result = (
                await _image_result(arguments, args)
                if name == "task_context_images" else _result(await _call(name, arguments, args))
            )
        except Exception as exc:
            result = _result({"error": type(exc).__name__, "detail": str(exc)[:250]}, error=True)
    elif method in {"resources/list", "prompts/list"}:
        result = {"resources" if method == "resources/list" else "prompts": []}
    elif method == "ping":
        result = {}
    else:
        return {
            "jsonrpc": "2.0", "id": request_id,
            "error": {"code": -32601, "message": "Method not found"},
        }
    return {"jsonrpc": "2.0", "id": request_id, "result": result}


async def _main() -> None:
    # MCP stdio is UTF-8, regardless of Windows' default console code page.
    # Otherwise native Unicode tool arguments become mojibake before validation.
    sys.stdin.reconfigure(encoding="utf-8")
    sys.stdout.reconfigure(encoding="utf-8", newline="\n")
    args = _parser().parse_args()
    settings.task_project_roots = args.project_roots
    if args.local_broker:
        settings.task_command_mode = "trusted"
    if args.workflow_schema:
        settings.task_workflow_allowlist = args.workflow_allowlist
    if args.context_blobs_dir:
        settings.task_context_blobs_dir = args.context_blobs_dir
    if args.apps_database_url:
        settings.apps_database_url = args.apps_database_url
    if args.apps_dir:
        settings.apps_dir = args.apps_dir
    initialize_engine(args.database_url)
    try:
        while True:
            line = await asyncio.to_thread(sys.stdin.readline)
            if not line:
                return
            try:
                response = await _handle(json.loads(line), args)
            except Exception as exc:
                response = {
                    "jsonrpc": "2.0", "id": None,
                    "error": {"code": -32603, "message": type(exc).__name__},
                }
            if response is not None:
                sys.stdout.write(json.dumps(response, ensure_ascii=True, separators=(",", ":")) + "\n")
                sys.stdout.flush()
    finally:
        await close_engine()


if __name__ == "__main__":
    asyncio.run(_main())
