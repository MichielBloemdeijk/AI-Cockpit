"""Bootstrap and scaffold helpers for generated frontend apps."""
from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
import re

from app.config import settings
from app.models.chat import Message
from app.services.chat_settings import chat_settings_service
from app.services.app_registry import GeneratedAppRecord, app_registry_service, resolve_generated_app_contract
from app.services.llm import PromptSegment, chat_completion_structured


@dataclass(slots=True)
class AppBootstrapResult:
    app: GeneratedAppRecord
    created: bool
    scaffolded_files: list[str]
    route_path: str
    frontend_root: str
    frontend_entry_path: str
    frontend_layout_path: str
    manifest_path: str
    asset_root: str
    allowed_write_roots: list[str]


def _repo_root() -> Path:
    return settings.backend_root.parent.resolve()


def _display_path(path: Path) -> str:
    return path.resolve().relative_to(_repo_root()).as_posix()


def _derive_title(goal: str) -> str:
    normalized = " ".join(goal.split()).strip()
    if not normalized:
        return "Generated App"

    lower = normalized.lower()
    for marker in (" called ", " named "):
        if marker in lower:
            tail = normalized[lower.index(marker) + len(marker):].strip(" .:-")
            if tail:
                return tail[:80]

    says_match = re.search(r"\b(?:that\s+)?(?:says|showing|with\s+text)\s+(.+)$", normalized, flags=re.IGNORECASE)
    if says_match:
        phrase = says_match.group(1).strip(" .:-\"'")
        if phrase:
            return phrase[:80].title()

    cleaned = re.sub(
        r"^(?:please\s+)?(?:can\s+you\s+|could\s+you\s+|would\s+you\s+)?"
        r"(?:create|build|make|develop|generate|design|prototype|spin\s+up)\s+",
        "",
        normalized,
        flags=re.IGNORECASE,
    )
    cleaned = re.sub(
        r"^(?:a|an|the)\s+(?:basic|simple|new|small|modern|clean)\s+",
        "",
        cleaned,
        flags=re.IGNORECASE,
    )
    cleaned = re.sub(
        r"^(?:app|web\s*app|web\s*page|website|site|dashboard|landing\s*page|frontend|ui)\b\s*",
        "",
        cleaned,
        flags=re.IGNORECASE,
    )
    cleaned = re.sub(r"^(?:for|to|that|which)\s+", "", cleaned, flags=re.IGNORECASE)
    cleaned = cleaned.strip(" .:-")
    if cleaned:
        return cleaned[:80].title()

    return normalized[:80]


def _slugify(value: str) -> str:
    normalized = re.sub(r"[^a-zA-Z0-9]+", "-", value.strip().lower()).strip("-")
    return normalized or "app"


def _title_from_slug(value: str) -> str:
  words = [part for part in re.split(r"[-_\s]+", value.strip()) if part]
  if not words:
    return "Generated App"
  return " ".join(word[:1].upper() + word[1:] for word in words)[:80]


async def _generate_title(goal: str) -> str:
    normalized_goal = " ".join(goal.split()).strip()
    if not normalized_goal:
        return "Generated App"

    model = await chat_settings_service.get_task_agent_model()
    system_prompt = (
        "You name generated apps for AI Cockpit. Return a concise product-style app title as JSON. "
        "Do not echo the whole request. Avoid filler like 'Create', 'Build', or 'App That'. "
        "Prefer 2-4 words, title case, specific and natural."
    )
    user_prompt = (
        f"User request:\n{normalized_goal}\n\n"
        "Respond with a concise app title that a human would actually want to see in the UI."
    )
    prompt_segments = [
        PromptSegment(role="system", text=system_prompt, cache_candidate=True, stable=True),
        PromptSegment(role="user", text=user_prompt),
    ]
    payload, _response = await chat_completion_structured(
        [
            Message(role="system", content=system_prompt),
            Message(role="user", content=user_prompt),
        ],
        model,
        schema_name="generated_app_name",
        json_schema={
            "type": "object",
            "properties": {
                "title": {
                    "type": "string",
                    "minLength": 1,
                    "maxLength": 80,
                }
            },
            "required": ["title"],
            "additionalProperties": False,
        },
        temperature=0.2,
        max_tokens=120,
        session_id=f"app-naming:{_slugify(normalized_goal)[:48]}",
        prompt_segments=prompt_segments,
    )
    title = str(payload.get("title") or "").strip().strip("\"'")
    return title[:80] or _derive_title(normalized_goal)


async def _next_available_identity(base_title: str) -> tuple[str, str]:
    existing = await app_registry_service.list_apps()
    used_slugs = {item.slug for item in existing}

    index = 1
    while True:
        candidate_title = base_title if index == 1 else f"{base_title} {index}"
        candidate_slug = _slugify(candidate_title)
        if candidate_slug not in used_slugs:
            return candidate_title, candidate_slug
        index += 1


def _manifest_payload(app: GeneratedAppRecord, bootstrap: AppBootstrapResult) -> dict[str, object]:
    return {
        "id": app.id,
        "slug": app.slug,
        "title": app.title,
        "description": app.description,
        "route_path": bootstrap.route_path,
        "frontend_root": bootstrap.frontend_root,
        "frontend_entry_path": bootstrap.frontend_entry_path,
        "frontend_layout_path": bootstrap.frontend_layout_path,
        "asset_root": bootstrap.asset_root,
        "allowed_write_roots": bootstrap.allowed_write_roots,
        "conventions": {
            "default_entry": "page.tsx at the app root",
            "nested_pages": "Create additional page.tsx files under nested folders inside this app root",
            "components_dir": "components/",
            "lib_dir": "lib/",
            "styles_file": "styles.css",
            "assets_public_root": bootstrap.asset_root,
        },
    }


def _root_layout_template(app: GeneratedAppRecord) -> str:
    return f'''import "./styles.css";

export default function {app.slug.replace("-", " ").title().replace(" ", "")}Layout({{ children }}: {{ children: React.ReactNode }}) {{
  return children;
}}
'''


def _page_template(app: GeneratedAppRecord, route_path: str) -> str:
    description = app.description or "This page was scaffolded for the task agent. Replace it with the real app UI and add nested routes under this directory as needed."
    return f'''export default function {app.slug.replace("-", " ").title().replace(" ", "")}Page() {{
  return (
    <section className="generated-app-card">
      <span className="generated-app-badge">Draft Surface Ready</span>
      <h2>{app.title}</h2>
      <p>{description}</p>
      <div className="generated-app-meta">
        <div>
          <span>Route</span>
          <strong>{route_path}</strong>
        </div>
        <div>
          <span>Next Step</span>
          <strong>Edit this page or add nested page.tsx files under this app directory.</strong>
        </div>
      </div>
    </section>
  );
}}
'''


def _loading_template() -> str:
    return '''export default function Loading() {
  return <div className="generated-app-card">Loading generated app...</div>;
}
'''


def _error_template() -> str:
    return '''"use client";

export default function Error({ error, reset }: { error: Error; reset: () => void }) {
  return (
    <div className="generated-app-card generated-app-error">
      <h2>Generated app error</h2>
      <p>{error.message}</p>
      <button type="button" onClick={reset} className="generated-app-button">
        Retry
      </button>
    </div>
  );
}
'''


def _styles_template() -> str:
    return '''.generated-app-shell {
  min-height: 100dvh;
  display: flex;
  flex-direction: column;
  background:
    radial-gradient(circle at top, rgba(59, 130, 246, 0.18), transparent 40%),
    linear-gradient(180deg, #0f172a 0%, #09090b 100%);
  color: #f8fafc;
}

.generated-app-header {
  width: 100%;
  padding: 24px 24px 0;
}

.generated-app-kicker {
  margin: 0 0 8px;
  letter-spacing: 0.18em;
  text-transform: uppercase;
  font-size: 11px;
  color: rgba(148, 163, 184, 0.9);
}

.generated-app-header h1 {
  margin: 0;
  font-size: clamp(2rem, 4vw, 3.25rem);
}

.generated-app-main {
  flex: 1;
  display: flex;
  width: 100%;
  min-height: 0;
  padding: 0;
}

.generated-app-card {
  flex: 1;
  width: 100%;
  min-height: 100%;
  border: none;
  background: transparent;
  border-radius: 0;
  padding: 28px;
  box-shadow: none;
}

.generated-app-card h2 {
  margin: 16px 0 12px;
  font-size: clamp(1.5rem, 3vw, 2.25rem);
}

.generated-app-card p {
  margin: 0;
  color: rgba(226, 232, 240, 0.84);
  line-height: 1.7;
}

.generated-app-badge {
  display: inline-flex;
  padding: 8px 12px;
  border-radius: 999px;
  background: rgba(59, 130, 246, 0.16);
  border: 1px solid rgba(59, 130, 246, 0.35);
  font-size: 12px;
  font-weight: 600;
}

.generated-app-meta {
  display: grid;
  gap: 16px;
  margin-top: 24px;
}

.generated-app-meta div {
  border: 1px solid rgba(148, 163, 184, 0.14);
  border-radius: 20px;
  padding: 16px;
  background: rgba(2, 6, 23, 0.42);
}

.generated-app-meta span {
  display: block;
  margin-bottom: 6px;
  font-size: 11px;
  text-transform: uppercase;
  letter-spacing: 0.16em;
  color: rgba(148, 163, 184, 0.88);
}

.generated-app-meta strong {
  display: block;
  font-size: 14px;
  line-height: 1.5;
}

.generated-app-error {
  border-color: rgba(251, 113, 133, 0.35);
}

.generated-app-button {
  margin-top: 16px;
  border: 0;
  border-radius: 999px;
  padding: 10px 16px;
  background: #2563eb;
  color: white;
  cursor: pointer;
}

@media (max-width: 640px) {
  .generated-app-header {
    padding: 16px 16px 0;
  }

  .generated-app-card {
    padding: 20px;
  }
}
'''


def _readme_template(app: GeneratedAppRecord, bootstrap: AppBootstrapResult) -> str:
    return f'''# {app.title}

This generated app lives at `{bootstrap.route_path}`.

Agent contract:
- Main entry: `{bootstrap.frontend_entry_path}`
- App layout: `{bootstrap.frontend_layout_path}`
- Manifest: `{bootstrap.manifest_path}`
- Asset root: `{bootstrap.asset_root}`
- Allowed write roots: {json.dumps(bootstrap.allowed_write_roots)}

Suggested organization:
- Put shared app UI in `components/`
- Put app-specific helpers in `lib/`
- Add nested routes by creating more `page.tsx` files inside subfolders under this app root
- Prefer the existing `styles.css`; if you add a CSS Module, every selector must include a local class or id instead of bare elements like `h1`, and keep `@keyframes` plus selectors like `0%` or `100%` in `styles.css` rather than the module file
- Put public assets under `{bootstrap.asset_root}`
'''


def _ensure_text_file(path: Path, content: str) -> bool:
    if path.exists():
        return False
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    return True


def _ensure_directory(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)


async def bootstrap_generated_app(
    *,
    goal: str,
    title: str | None = None,
    app_slug: str | None = None,
    description: str | None = None,
    source_conversation_id: str | None = None,
    source_task_run_id: str | None = None,
  contract_override: dict[str, str] | None = None,
) -> AppBootstrapResult:
  if title and title.strip():
    derived_title = title.strip()
  elif app_slug and app_slug.strip():
    derived_title = _title_from_slug(app_slug)
  else:
    derived_title = await _generate_title(goal)

  created = False

  if app_slug and app_slug.strip():
    existing = await app_registry_service.get_app_by_slug(app_slug)
    if existing is None:
      existing = await app_registry_service.create_app(
        title=derived_title,
        slug=app_slug,
        description=description or goal.strip(),
        status="building",
        source_conversation_id=source_conversation_id,
        source_task_run_id=source_task_run_id,
        contract_override=contract_override,
      )
      created = True
    else:
      updated = await app_registry_service.update_app(
        existing.id,
        status="building",
        description=description or existing.description or goal.strip(),
        source_conversation_id=source_conversation_id,
        source_task_run_id=source_task_run_id,
      )
      if updated is not None:
        existing = updated
  else:
    candidate_title, candidate_slug = await _next_available_identity(derived_title)
    existing = await app_registry_service.create_app(
      title=candidate_title,
      slug=candidate_slug,
      description=description or goal.strip(),
      status="building",
      source_conversation_id=source_conversation_id,
      source_task_run_id=source_task_run_id,
      contract_override=contract_override,
    )
    created = True

  contract = resolve_generated_app_contract(existing.slug, existing.manifest_json)
  frontend_root = _repo_root() / contract.frontend_root
  asset_root = _repo_root() / contract.asset_root
  components_dir = frontend_root / "components"
  lib_dir = frontend_root / "lib"

  _ensure_directory(frontend_root)
  _ensure_directory(asset_root)
  _ensure_directory(components_dir)
  _ensure_directory(lib_dir)

  bootstrap = AppBootstrapResult(
    app=existing,
    created=created,
    scaffolded_files=[],
    route_path=contract.route_path,
    frontend_root=contract.frontend_root,
    frontend_entry_path=contract.frontend_entry_path,
    frontend_layout_path=contract.frontend_layout_path,
    manifest_path=contract.manifest_path,
    asset_root=contract.asset_root,
    allowed_write_roots=contract.allowed_write_roots,
  )

  files = {
    frontend_root / "layout.tsx": _root_layout_template(existing),
    frontend_root / "page.tsx": _page_template(existing, contract.route_path),
    frontend_root / "loading.tsx": _loading_template(),
    frontend_root / "error.tsx": _error_template(),
    frontend_root / "styles.css": _styles_template(),
    frontend_root / "cockpit-app.json": json.dumps(_manifest_payload(existing, bootstrap), indent=2),
    frontend_root / "README.md": _readme_template(existing, bootstrap),
  }
  for file_path, content in files.items():
    if _ensure_text_file(file_path, content + ("\n" if not content.endswith("\n") else "")):
      bootstrap.scaffolded_files.append(_display_path(file_path))

  for marker_path in (components_dir / ".gitkeep", lib_dir / ".gitkeep", asset_root / ".gitkeep"):
    if _ensure_text_file(marker_path, ""):
      bootstrap.scaffolded_files.append(_display_path(marker_path))

  updated = await app_registry_service.update_app(
    existing.id,
    frontend_entry_path=contract.frontend_entry_path,
    status="building",
    source_conversation_id=source_conversation_id,
    source_task_run_id=source_task_run_id,
    manifest_json=_manifest_payload(existing, bootstrap),
  )
  if updated is not None:
    bootstrap.app = updated

  return bootstrap

