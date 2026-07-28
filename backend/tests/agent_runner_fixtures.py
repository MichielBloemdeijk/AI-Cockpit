from __future__ import annotations

import json

from app.models.chat import ModelResponse, ToolCall, ToolCallFunction
from app.services.conversation_store import conversation_store


APPROVED_PLAN = {
    "summary": "Inspect the target file and report back.",
    "steps": ["Inspect the target file.", "Summarize the outcome."],
    "open_questions": [],
    "assumptions": [],
    "approved": True,
    "feedback_skipped": True,
    "approved_at": "2026-05-06T00:00:00Z",
    "feedback": None,
}


async def create_agent_run(
    model: str = "anthropic/claude-sonnet-4-5",
    *,
    current_step: int = 0,
    silent_tool_call_streak: int = 0,
):
    conversation = await conversation_store.create_conversation(mode_hint="single")
    run = await conversation_store.start_run(
        conversation.id,
        kind="assistant",
        metadata_json={
            "task_type": "agent",
            "title": "Agent run",
            "goal": "Inspect frontend/lib/hooks.ts and summarize the result.",
            "plan": dict(APPROVED_PLAN),
            "skip_plan_feedback": True,
            "model": model,
            "current_step": current_step,
            "silent_tool_call_streak": silent_tool_call_streak,
            "history": [],
            "payload": {},
            "branch_key": "main",
        },
    )
    return conversation, run


async def create_unplanned_agent_run(model: str = "anthropic/claude-haiku-4.5"):
    conversation = await conversation_store.create_conversation(mode_hint="single")
    run = await conversation_store.start_run(
        conversation.id,
        kind="assistant",
        metadata_json={
            "task_type": "agent",
            "title": "Agent run",
            "goal": "Create Hello World App 9.",
            "skip_plan_feedback": True,
            "model": model,
            "history": [],
            "payload": {},
            "branch_key": "main",
        },
    )
    return conversation, run


def tool_response(tool_name: str, arguments: dict[str, object], *, content: str = "", reasoning: str = "") -> ModelResponse:
    return ModelResponse(
        model="anthropic/claude-sonnet-4-5",
        content=content,
        reasoning=reasoning,
        usage=None,
        tool_calls=[
            ToolCall(
                id="call_tool",
                function=ToolCallFunction(
                    name=tool_name,
                    arguments=json.dumps(arguments),
                ),
            )
        ],
        finish_reason="tool_calls",
    )


def finalize_response(result: str = "Finished") -> ModelResponse:
    return ModelResponse(
        model="anthropic/claude-sonnet-4-5",
        content=result,
        usage=None,
        tool_calls=[
            ToolCall(
                id="call_finalize",
                function=ToolCallFunction(
                    name="task_finalize",
                    arguments=json.dumps({"summary": result}),
                ),
            )
        ],
        finish_reason="tool_calls",
    )


def no_tool_response(content: str, *, finish_reason: str = "stop") -> ModelResponse:
    return ModelResponse(
        model="anthropic/claude-sonnet-4-5",
        content=content,
        usage=None,
        tool_calls=[],
        finish_reason=finish_reason,
    )
