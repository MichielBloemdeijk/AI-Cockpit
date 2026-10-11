from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import sys
import time
from typing import Any


TOOL_NAME = "journal_commit"


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()
    parser.add_argument("--journal", type=Path, required=True)
    parser.add_argument("--operation-id", required=True)
    return parser


def _write_message(message: dict[str, Any]) -> None:
    payload = json.dumps(message, separators=(",", ":"), ensure_ascii=True)
    sys.stdout.write(payload + "\n")
    sys.stdout.flush()


def _tool_result(*, is_error: bool, payload: dict[str, Any]) -> dict[str, Any]:
    return {
        "content": [
            {
                "type": "text",
                "text": json.dumps(payload, separators=(",", ":"), ensure_ascii=True),
            }
        ],
        "structuredContent": payload,
        "isError": is_error,
    }


def _commit(journal: Path, operation_id: str, value: str) -> dict[str, Any]:
    record = {
        "schema": "jarvis-m0-operation.v1",
        "operation_id": operation_id,
        "value": value,
        "state": "succeeded",
        "committed_ns": time.time_ns(),
    }
    journal.parent.mkdir(parents=True, exist_ok=True)
    with journal.open("a", encoding="utf-8", newline="\n") as handle:
        handle.write(json.dumps(record, separators=(",", ":"), ensure_ascii=True) + "\n")
        handle.flush()
        os.fsync(handle.fileno())
    return record


def _handle_request(
    request: dict[str, Any], *, journal: Path, allowed_operation_id: str
) -> dict[str, Any] | None:
    request_id = request.get("id")
    method = request.get("method")
    if request_id is None:
        return None
    if method == "initialize":
        requested_version = (request.get("params") or {}).get("protocolVersion")
        return {
            "jsonrpc": "2.0",
            "id": request_id,
            "result": {
                "protocolVersion": requested_version or "2024-11-05",
                "capabilities": {"tools": {"listChanged": False}},
                "serverInfo": {"name": "jarvis-m0-fixture", "version": "1"},
            },
        }
    if method == "tools/list":
        return {
            "jsonrpc": "2.0",
            "id": request_id,
            "result": {
                "tools": [
                    {
                        "name": TOOL_NAME,
                        "description": (
                            "Persist one authorized probe operation before acknowledging success."
                        ),
                        "inputSchema": {
                            "type": "object",
                            "properties": {
                                "operation_id": {"type": "string"},
                                "value": {"type": "string"},
                            },
                            "required": ["operation_id", "value"],
                            "additionalProperties": False,
                        },
                    }
                ]
            },
        }
    if method == "tools/call":
        params = request.get("params") or {}
        name = params.get("name")
        arguments = params.get("arguments") or {}
        supplied_operation_id = arguments.get("operation_id")
        value = arguments.get("value")
        if name != TOOL_NAME:
            result = _tool_result(
                is_error=True,
                payload={"error": "tool_not_allowlisted", "tool": name},
            )
        elif supplied_operation_id != allowed_operation_id:
            result = _tool_result(
                is_error=True,
                payload={"error": "operation_not_authorized"},
            )
        elif not isinstance(value, str):
            result = _tool_result(
                is_error=True,
                payload={"error": "invalid_value"},
            )
        else:
            result = _tool_result(
                is_error=False,
                payload=_commit(journal, allowed_operation_id, value),
            )
        return {"jsonrpc": "2.0", "id": request_id, "result": result}
    if method in {"resources/list", "prompts/list"}:
        key = "resources" if method == "resources/list" else "prompts"
        return {"jsonrpc": "2.0", "id": request_id, "result": {key: []}}
    if method == "ping":
        return {"jsonrpc": "2.0", "id": request_id, "result": {}}
    return {
        "jsonrpc": "2.0",
        "id": request_id,
        "error": {"code": -32601, "message": f"Method not found: {method}"},
    }


def main() -> int:
    args = _parser().parse_args()
    for raw_line in sys.stdin:
        line = raw_line.strip()
        if not line:
            continue
        try:
            request = json.loads(line)
            response = _handle_request(
                request,
                journal=args.journal,
                allowed_operation_id=args.operation_id,
            )
        except Exception as exc:  # pragma: no cover - defensive protocol boundary
            response = {
                "jsonrpc": "2.0",
                "id": None,
                "error": {"code": -32603, "message": f"fixture failure: {type(exc).__name__}"},
            }
        if response is not None:
            _write_message(response)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
