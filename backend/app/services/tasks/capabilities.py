"""Trusted capability identities; definitions select, never implement, tools."""
from __future__ import annotations


CAPABILITY_TOOLS: dict[str, tuple[str, ...]] = {
    "task.context.read@1": ("task_history",),
    "task.context.images@2": ("task_context_images",),
    "research.native@1": (),
    "task.question.ask@1": ("task_question",),
    "task.result.publish@1": ("result_publish",),
    "task.finish@1": ("task_finish",),
    "app.package@1": ("app_checkout", "app_catalog", "app_check"),
}

INPUT_CONTEXTS = {
    "text-request.v1": (),
    "screen-request.v2": ("screen_images",),
}


def workflow_tools(pinned: dict) -> list[str]:
    return [tool for capability in pinned["capabilities"] for tool in CAPABILITY_TOOLS[capability]]


def validate_capabilities(capabilities: object, contexts: tuple[str, ...]) -> tuple[str, ...]:
    if (not isinstance(capabilities, list) or not capabilities
            or any(not isinstance(item, str) or item not in CAPABILITY_TOOLS for item in capabilities)
            or len(capabilities) != len(set(capabilities))):
        raise ValueError("Workflow capabilities are not registered or contain duplicates")
    selected = set(capabilities)
    if not {"task.context.read@1", "task.result.publish@1"} <= selected:
        raise ValueError("Workflow requires context and publication capabilities")
    if ("task.context.images@2" in selected) != ("screen_images" in contexts):
        raise ValueError("Workflow image capability and context requirements disagree")
    return tuple(capabilities)
