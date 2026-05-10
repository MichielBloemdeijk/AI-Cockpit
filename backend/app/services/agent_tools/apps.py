from __future__ import annotations

from typing import Any

from app.services.agent_tools.base import AgentTool
from app.services.agent_tools.base import ToolExecutionContext
from app.services.agent_tools.base import ToolExecutionResult
from app.services.agent_tools.base import _tool_property
from app.services.agent_tools.base import _tool_schema
from app.services.app_builder import bootstrap_generated_app
from app.services.app_registry import app_registry_service


async def run_app_initialize(
    *,
    context: ToolExecutionContext,
    title: str | None = None,
    app_slug: str | None = None,
    description: str | None = None,
) -> ToolExecutionResult:
    normalized_title = (title or "").strip()
    normalized_slug = (app_slug or "").strip()
    if not normalized_title and not normalized_slug:
        raise ValueError("App initialization requires title or app_slug")
    bootstrap = await bootstrap_generated_app(
        goal=normalized_title or normalized_slug,
        title=normalized_title or None,
        app_slug=normalized_slug or None,
        description=description,
        source_conversation_id=context.conversation_id,
        source_task_run_id=context.run_id,
        contract_override=context.generated_app_contract_override,
    )
    bootstrap.app = await app_registry_service.acquire_lease(
        app_id=bootstrap.app.id,
        conversation_id=context.conversation_id,
        holder_run_id=context.run_id,
    )
    mode = "created" if bootstrap.created else "attached"
    output = f"{mode.title()} generated app '{bootstrap.app.title}' at {bootstrap.route_path}."

    return ToolExecutionResult(
        tool="app_initialize",
        output=output,
        metadata={
            "mode": mode,
            "app": {
                "app_id": bootstrap.app.id,
                "slug": bootstrap.app.slug,
                "title": bootstrap.app.title,
                "description": bootstrap.app.description,
                "route_path": bootstrap.route_path,
                "frontend_root": bootstrap.frontend_root,
                "frontend_entry_path": bootstrap.frontend_entry_path,
                "frontend_layout_path": bootstrap.frontend_layout_path,
                "manifest_path": bootstrap.manifest_path,
                "asset_root": bootstrap.asset_root,
                "allowed_write_roots": bootstrap.allowed_write_roots,
                "scaffolded_files": bootstrap.scaffolded_files,
            },
            "write_roots": bootstrap.allowed_write_roots,
        },
    )


async def run_app_list(*, context: ToolExecutionContext) -> ToolExecutionResult:
    apps = await app_registry_service.list_apps()
    items = [
        {
            "id": app.id,
            "slug": app.slug,
            "title": app.title,
            "route_path": app.route_path,
            "status": app.status,
        }
        for app in apps
    ]
    if not items:
        output = "No generated apps are registered yet."
    else:
        output = "Registered apps:\n" + "\n".join(
            f"- {item['title']} ({item['slug']}) [{item['status']}]" for item in items
        )
    return ToolExecutionResult(
        tool="app_list",
        output=output,
        metadata={"apps": items, "count": len(items), "workspace_path": context.workspace_path},
    )


async def _execute_app_initialize(context: ToolExecutionContext, arguments: dict[str, Any]) -> ToolExecutionResult:
    return await run_app_initialize(
        context=context,
        title=None if arguments.get("title") in (None, "") else str(arguments.get("title")),
        app_slug=None if arguments.get("app_slug") in (None, "") else str(arguments.get("app_slug")),
        description=None if arguments.get("description") in (None, "") else str(arguments.get("description")),
    )


async def _execute_app_list(context: ToolExecutionContext, arguments: dict[str, Any]) -> ToolExecutionResult:
    return await run_app_list(context=context)


APP_TOOLS: list[AgentTool] = [
    AgentTool(
        name="app_initialize",
        description="Create or attach to a generated app route under /apps/<slug>, scaffold the standard files if missing, and return the allowed app write roots. For a new app, pass a clear title that should become the visible app name; optionally pass app_slug when you need an exact route segment. For attaching an existing app, pass app_slug.",
        input_schema=_tool_schema(
            {
                "title": _tool_property("string", "Visible app title to use when creating a new generated app."),
                "app_slug": _tool_property("string", "Existing app slug to attach to, or explicit slug to use for a new app."),
                "description": _tool_property("string", "Optional short description of the generated app."),
            },
        ),
        executor=_execute_app_initialize,
        read_only=False,
    ),
    AgentTool(
        name="app_list",
        description="List generated apps with title, slug, route path, and status so you can pick a unique name before creating a new app.",
        input_schema=_tool_schema({}),
        executor=_execute_app_list,
        read_only=True,
    ),
]


__all__ = [
    "APP_TOOLS",
    "run_app_initialize",
    "run_app_list",
]