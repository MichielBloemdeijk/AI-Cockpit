"""Stdio bridge for backend-bound background capabilities (no native I/O)."""
from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from app.db.session import close_engine, initialize_engine, session_scope
from app.db.tables import OrchestrationDecision
from app.services.task_orchestration import DecisionClaim, DecisionTools


def catalog() -> list[dict]:
    from app.services.task_execution_preferences import preferences_schema
    execution = {"execution_preferences": preferences_schema()}
    common = {"operation_key": {"type": "string", "minLength": 1, "maxLength": 128},
              "reason": {"type": "string", "minLength": 1, "maxLength": 2000}}
    shapes = {
        "inspect": ({"after_sequence": {"type": "integer", "minimum": 0},
                     "event_sequence": {"type": "integer", "minimum": 1},
                     "turn_sequence": {"type": "integer", "minimum": 1},
                     "artifact_id": {"type": "string", "minLength": 1, "maxLength": 36},
                     "instruction_id": {"type": "string", "minLength": 1, "maxLength": 36},
                     "instructions_after": {"type": "string", "maxLength": 36},
                     "task_goal": {"type": "boolean", "const": True},
                     "byte_offset": {"type": "integer", "minimum": 0}}, [],
                    "Read bounded task evidence or approved standing instructions. Select one original event, turn, artifact or instruction and page its JSON by byte offset. List further instructions using instructions_after (empty string starts the catalog). Never read a native file or URL."),
        "answer": ({**common, "answer": {"type": "string", "minLength": 1}}, ["answer", *common], "Answer the exact worker question only from supported instructions/evidence; resumes its asking run."),
        "control": ({**common, "action": {"type": "string", "enum": ["retry", "set_importance", "suspend", "yield"]}, "importance": {"type": "string", "enum": ["low", "normal", "high"]}}, ["action", *common], "Apply a supported task control. Never override user pause, cancel or permissions."),
        "continue": ({**common, **execution, "content": {"type": "string", "minLength": 1}}, ["content", *common], "Continue through the common turn API. Optional permitted model/effort apply only to the new run; omitted preferences inherit the prior run."),
        "delegate": ({**common, **execution, "goal": {"type": "string", "minLength": 1}}, ["goal", *common], "Delegate substantial repair to the ordinary worker queue, optionally choosing a permitted model/effort; never change permissions or repeat an unchanged failed repair."),
        "retain": (common, list(common), "Retain the unresolved question/failure in the existing user attention inbox. Never treat announcement as an answer."),
        "presentation": ({**common, "policy": {"type": "string", "enum": ["critical_only", "follow_progress", "silent"]}},
                         ["policy", *common], "Set this task's supported update preference and retain its unresolved issue through the common inbox. This never answers a question."),
    }
    return [{"name": name, "description": description, "inputSchema": {
        "type": "object", "properties": properties, "required": required, "additionalProperties": False,
    }} for name, (properties, required, description) in shapes.items()]


async def handle(request: dict, tools: DecisionTools) -> dict | None:
    if request.get("id") is None:
        return None
    method = request.get("method")
    if method == "initialize":
        result = {"protocolVersion": (request.get("params") or {}).get("protocolVersion", "2024-11-05"),
                  "capabilities": {"tools": {}}, "serverInfo": {"name": "jarvis-orchestration", "version": "1"}}
    elif method == "tools/list":
        result = {"tools": catalog()}
    elif method == "tools/call":
        params = request.get("params") or {}
        try:
            args = params.get("arguments") or {}
            if not isinstance(args, dict):
                raise ValueError("Arguments must be an object")
            output = await tools.call(str(params.get("name") or ""), args)
            error = False
        except Exception as exc:
            output, error = {"error": type(exc).__name__, "detail": str(exc)[:250]}, True
        result = {"content": [{"type": "text", "text": json.dumps(output, ensure_ascii=True)}],
                  "structuredContent": output, "isError": error}
    elif method in {"resources/list", "prompts/list"}:
        result = {"resources" if method == "resources/list" else "prompts": []}
    elif method == "ping":
        result = {}
    else:
        return {"jsonrpc": "2.0", "id": request["id"], "error": {"code": -32601, "message": "Method not found"}}
    return {"jsonrpc": "2.0", "id": request["id"], "result": result}


async def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--database-url", required=True)
    parser.add_argument("--decision-id", required=True)
    parser.add_argument("--generation", type=int, required=True)
    parser.add_argument("--owner", required=True)
    args = parser.parse_args()
    initialize_engine(args.database_url)
    async with session_scope() as session:
        decision = await session.get(OrchestrationDecision, args.decision_id)
        if (decision is None or decision.status != "running" or decision.claim_owner != args.owner
                or decision.claim_generation != args.generation):
            raise ValueError("Stale orchestration bridge identity")
        claim = DecisionClaim(decision.id, args.generation, args.owner, dict(decision.context_json))
    tools = DecisionTools(claim)
    try:
        while line := await asyncio.to_thread(sys.stdin.readline):
            response = await handle(json.loads(line), tools)
            if response is not None:
                sys.stdout.write(json.dumps(response, ensure_ascii=True) + "\n")
                sys.stdout.flush()
    finally:
        await close_engine()


if __name__ == "__main__":
    asyncio.run(main())
