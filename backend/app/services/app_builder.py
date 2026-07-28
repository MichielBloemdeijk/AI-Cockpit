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
  return <div className="generated-app-shell">{{children}}</div>;
}}
'''


def _page_template(app: GeneratedAppRecord, _route_path: str) -> str:
    title = json.dumps(app.title, ensure_ascii=False)
    description = json.dumps(app.description or "A focused space for your next idea.", ensure_ascii=False)
    return f'''export default function {app.slug.replace("-", " ").title().replace(" ", "")}Page() {{
  return (
    <main className="generated-app-home">
      <section className="generated-app-intro" aria-labelledby="app-title">
        <h1 id="app-title">{{{title}}}</h1>
        <p>{{{description}}}</p>
      </section>
    </main>
  );
}}
'''


def _loading_template() -> str:
    return '''export default function Loading() {
  return (
    <main className="generated-app-home">
      <div className="generated-app-state" role="status">Loading…</div>
    </main>
  );
}
'''


def _error_template() -> str:
    return '''"use client";

export default function Error({ error, reset }: { error: Error; reset: () => void }) {
  return (
    <main className="generated-app-home">
      <section className="generated-app-state generated-app-error">
        <h1>Something went wrong</h1>
        <p>{error.message}</p>
        <button type="button" onClick={reset} className="generated-app-button">Try again</button>
      </section>
    </main>
  );
}
'''


def _styles_template() -> str:
    return '''.generated-app-shell {
  --app-background: #f4f4f5;
  --app-surface: rgba(255, 255, 255, 0.78);
  --app-border: rgba(24, 24, 27, 0.09);
  --app-text: #18181b;
  --app-muted: #52525b;
  min-height: 100dvh;
  position: relative;
  overflow: hidden;
  background:
    radial-gradient(circle at 10% 5%, rgba(125, 211, 252, 0.36), transparent 34rem),
    radial-gradient(circle at 92% 18%, rgba(196, 181, 253, 0.32), transparent 30rem),
    var(--app-background);
  color: var(--app-text);
  font-family: Inter, ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
}

.generated-app-shell::before {
  content: "";
  position: absolute;
  inset: 0;
  pointer-events: none;
  background-image: linear-gradient(rgba(24, 24, 27, 0.025) 1px, transparent 1px), linear-gradient(90deg, rgba(24, 24, 27, 0.025) 1px, transparent 1px);
  background-size: 36px 36px;
  mask-image: linear-gradient(to bottom, black, transparent 72%);
}

.generated-app-home {
  position: relative;
  z-index: 1;
  min-height: 100dvh;
  display: grid;
  place-items: center;
  padding: clamp(1.25rem, 5vw, 4rem);
}

.generated-app-intro,
.generated-app-card,
.generated-app-state {
  width: min(100%, 56rem);
  border: 1px solid var(--app-border);
  border-radius: clamp(1.5rem, 4vw, 2.25rem);
  background: var(--app-surface);
  box-shadow: 0 24px 80px rgba(24, 24, 27, 0.12), inset 0 1px rgba(255, 255, 255, 0.7);
  backdrop-filter: blur(24px);
  padding: clamp(2rem, 7vw, 5.5rem);
}

.generated-app-intro h1,
.generated-app-card h1,
.generated-app-card h2,
.generated-app-state h1 {
  max-width: 14ch;
  margin: 0;
  font-size: clamp(2.5rem, 8vw, 5.75rem);
  font-weight: 650;
  line-height: 0.96;
  letter-spacing: -0.055em;
  text-wrap: balance;
}

.generated-app-intro p,
.generated-app-card p,
.generated-app-state p {
  max-width: 42rem;
  margin: clamp(1.25rem, 3vw, 2rem) 0 0;
  color: var(--app-muted);
  font-size: clamp(1rem, 2.25vw, 1.25rem);
  line-height: 1.7;
  text-wrap: pretty;
}

.generated-app-state {
  width: min(100%, 38rem);
  padding: clamp(1.75rem, 5vw, 3rem);
  color: var(--app-muted);
}

.generated-app-state h1 {
  color: var(--app-text);
  font-size: clamp(1.75rem, 5vw, 2.75rem);
  line-height: 1.05;
}

.generated-app-error {
  border-color: rgba(244, 63, 94, 0.22);
}

.generated-app-button {
  margin-top: 1.5rem;
  border: 0;
  border-radius: 999px;
  padding: 0.75rem 1.1rem;
  background: var(--app-text);
  color: white;
  font: inherit;
  font-weight: 600;
  cursor: pointer;
  transition: transform 150ms ease, opacity 150ms ease;
}

.generated-app-button:hover {
  transform: translateY(-1px);
  opacity: 0.86;
}

@media (prefers-color-scheme: dark) {
  .generated-app-shell {
    --app-background: #09090b;
    --app-surface: rgba(24, 24, 27, 0.78);
    --app-border: rgba(255, 255, 255, 0.09);
    --app-text: #fafafa;
    --app-muted: #a1a1aa;
    background:
      radial-gradient(circle at 10% 5%, rgba(14, 116, 144, 0.28), transparent 34rem),
      radial-gradient(circle at 92% 18%, rgba(109, 40, 217, 0.24), transparent 30rem),
      var(--app-background);
  }

  .generated-app-shell::before {
    background-image: linear-gradient(rgba(255, 255, 255, 0.025) 1px, transparent 1px), linear-gradient(90deg, rgba(255, 255, 255, 0.025) 1px, transparent 1px);
  }

  .generated-app-intro,
  .generated-app-card,
  .generated-app-state {
    box-shadow: 0 24px 80px rgba(0, 0, 0, 0.36), inset 0 1px rgba(255, 255, 255, 0.04);
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

