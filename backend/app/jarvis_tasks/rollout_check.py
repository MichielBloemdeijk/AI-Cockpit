"""Read-only preparation checks, never an authorization to enable production."""
from __future__ import annotations

from contextlib import closing
from datetime import datetime, timezone
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path
import os
import json
import sqlite3
import subprocess
import sys
import tempfile
from time import monotonic
from xml.etree import ElementTree

from alembic.config import Config
from alembic.script import ScriptDirectory

from app.config import Settings
from app.services.result_static import SHELL_FILES
from app.jarvis_tasks.sqlite_backup import snapshot_database


TABLES = ("tasks", "task_turns", "task_execution_runs", "task_attempts", "task_events",
          "result_pages", "result_page_revisions", "result_page_events", "app_data_records",
          "result_metrics", "notification_outbox", "cockpit_apps")

REHEARSAL_TESTS = ("tests/test_rollout_check.py", "tests/test_result_restart.py",
                   "tests/test_migration_recovery.py", "tests/test_orchestration_rollout.py", "tests/test_windows_job.py")

RETAINED_COLUMNS = {
    "tasks": ("id", "goal", "origin_surface", "idempotency_key"),
    "task_attempts": ("id", "task_id", "attempt_number", "workspace_path", "checkpoint_json"),
    "task_events": ("id", "task_id", "event_type", "payload_json", "idempotency_key"),
    "task_artifacts": ("id", "task_id", "artifact_type", "mime_type", "content_text", "content_json", "uri"),
    "task_questions": ("id", "task_id", "question", "answer", "state"),
    "task_turns": ("id", "task_id", "sequence", "role", "content", "request_identity"),
    "task_execution_runs": ("id", "task_id", "ordinal", "status", "current_attempt_id"),
    "result_pages": ("id", "public_id", "task_id", "current_revision_id", "revision_number"),
    "result_page_revisions": ("id", "result_page_id", "content_json"),
    "app_data_records": ("id", "cockpit_app_id", "scope_key", "namespace", "record_key", "value_json", "revision"),
}


def write_rollout_report(path: Path, report: dict) -> None:
    """Only replace our own same-schema JSON reports, never arbitrary files."""
    if path.suffix.lower() != ".json" or path.is_symlink():
        raise ValueError("Use a regular .json report destination")
    if path.exists():
        try:
            if path.stat().st_size > 2 * 1024 * 1024:
                raise ValueError("Existing file is not a bounded rollout report")
            existing = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError):
            raise ValueError("Refusing to replace an unrelated existing file") from None
        if not isinstance(existing, dict) or existing.get("schema") != report["schema"]:
            raise ValueError("Refusing to replace a different report or unrelated file")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")


def _retained_snapshot(path: Path, columns: dict | None = None) -> tuple[dict, dict]:
    selected, rows = {}, {}
    with closing(sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True, timeout=.1)) as connection:
        deadline = monotonic() + 10
        connection.set_progress_handler(lambda: int(monotonic() >= deadline), 1000)
        present = {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        for table, candidates in (columns or RETAINED_COLUMNS).items():
            if table not in present:
                if columns:
                    raise ValueError("Retained table is missing after migration")
                continue
            fields = {row[1] for row in connection.execute(f"PRAGMA table_info({table})")}
            selected[table] = tuple(field for field in candidates if field in fields)
            if columns and selected[table] != candidates:
                raise ValueError("Retained column is missing after migration")
            if selected[table]:
                rows[table] = connection.execute(f"SELECT {','.join(selected[table])} FROM {table} ORDER BY id").fetchall()
    return selected, rows


def migration_rehearsal(config: Settings) -> dict:
    """Snapshot the configured SQLite DB, migrate only its disposable clone.

    No profile/workspace copies; private rows and paths never reach the report.
    The clone is deleted after verification, not kept as a production backup.
    """
    report = {"schema": "jarvis-migration-rehearsal.v1", "status": "failed",
              "observed_at": datetime.now(timezone.utc).isoformat(), "provider_calls": 0,
              "production_mutations": [], "ready_for_enablement": False,
              "scope": "Configured database read-only snapshot, disposable-clone upgrade and retained-data checks; not deployment or complete backup"}
    source = config.database_path
    if source is None or not source.is_file():
        return report | {"failure": "sqlite_source_unavailable"}
    with tempfile.TemporaryDirectory(prefix="jarvis-migration-rehearsal-") as folder:
        clone = Path(folder) / "clone.db"
        try:
            proof = snapshot_database(source, clone)
            columns, before = _retained_snapshot(clone)
            env = {**os.environ, "DATABASE_URL": f"sqlite+aiosqlite:///{clone.as_posix()}",
                   "AUTH_PASSWORD": "", "JARVIS_ACCESS_MODE": "personal", "SESSION_SECRET_KEY": "isolated-migration-secret-not-production",
                   "TASK_POOL_ENABLED": "false", "TASK_ORCHESTRATION_ENABLED": "false",
                   "TASK_ORCHESTRATION_LIVE_ENABLED": "false", "TASK_RESEARCH_ENABLED": "false",
                   "OPENAI_API_KEY": "", "OPENROUTER_API_KEY": ""}
            process = subprocess.run([sys.executable, "-c", "from app.db.session import run_migrations; run_migrations()"],
                cwd=config.backend_root, env=env, capture_output=True, text=True, timeout=120)
            if process.returncode:
                return report | {"failure": "clone_migration_failed"}
            _, after = _retained_snapshot(clone, columns)
            stable = {table: before[table] == after.get(table) for table in before}
            clone_settings = Settings(_env_file=None, database_url=env["DATABASE_URL"])
            database = next(check for check in inspect_rollout(clone_settings)["checks"] if check["id"] == "database")
            passed = all(stable.values()) and database["status"] == "passed"
            return report | {"status": "passed" if passed else "failed", "snapshot": proof,
                             "database": database, "retained_tables": [{"table": table, "row_count": len(before[table]),
                                 "columns_checked": list(columns[table]), "unchanged": stable[table]} for table in before],
                             "clone_removed": True, "complete_backup": False}
        except (sqlite3.Error, OSError, ValueError, TimeoutError, subprocess.TimeoutExpired):
            return report | {"failure": "snapshot_or_verification_failed"}


def rehearsal_report(returncode: int, junit: Path) -> dict:
    checks = []
    try:
        suites = ElementTree.parse(junit).getroot()
        for case in suites.iter("testcase"):
            status = "failed" if case.find("failure") is not None or case.find("error") is not None else (
                "skipped" if case.find("skipped") is not None else "passed")
            checks.append({"name": case.attrib.get("name"), "status": status})
    except (OSError, ElementTree.ParseError):
        pass
    passed = bool(checks) and returncode == 0 and all(case["status"] == "passed" for case in checks)
    return {"schema": "jarvis-rollout-rehearsal.v1", "status": "passed" if passed else "failed",
            "observed_at": datetime.now(timezone.utc).isoformat(), "checks": checks,
            "tests_passed": sum(case["status"] == "passed" for case in checks),
            "provider_calls": 0, "production_mutations": [], "ready_for_enablement": False,
            "scope": "Isolated database snapshot/restore, API restart, migrations and disablement; not production backup or rollout acceptance"}


def run_rehearsal() -> dict:
    backend = Path(__file__).resolve().parents[2]
    with tempfile.TemporaryDirectory(prefix="jarvis-rollout-rehearsal-") as folder:
        root = Path(folder)
        env = {**os.environ, "DATABASE_URL": f"sqlite+aiosqlite:///{(root / 'unused.db').as_posix()}",
               "AUTH_PASSWORD": "", "JARVIS_ACCESS_MODE": "personal", "SESSION_SECRET_KEY": "isolated-rehearsal-secret-not-production",
               "TASK_POOL_ENABLED": "false", "TASK_ORCHESTRATION_ENABLED": "false",
               "TASK_ORCHESTRATION_LIVE_ENABLED": "false",
               "TASK_EXECUTOR": "legacy", "TASK_WORKFLOW_ALLOWLIST": "", "TASK_RESEARCH_ENABLED": "false",
               "OPENAI_API_KEY": "",
               "OPENROUTER_API_KEY": "", "WEB_PUSH_ENABLED": "false", "NTFY_TOPIC": "",
               "CODEX_PROFILES_DIR": str(root / "profiles"), "TASK_ORCHESTRATION_PROFILE": str(root / "background"),
               "TASK_ORCHESTRATION_CONTEXT_DIR": str(root / "context"), "PYTEST_ADDOPTS": ""}
        junit = root / "checks.xml"
        try:
            process = subprocess.run([sys.executable, "-m", "app.jarvis_tasks.rehearsal_host", *REHEARSAL_TESTS, "-q", "--tb=short",
                                      f"--junitxml={junit}"], cwd=backend, env=env,
                                     capture_output=True, text=True, timeout=300)
            return rehearsal_report(process.returncode, junit)
        except (subprocess.TimeoutExpired, OSError):
            return rehearsal_report(1, junit)


def _overlap(left: Path, right: Path) -> bool:
    return left == right or left in right.parents or right in left.parents


def inspect_rollout(config: Settings, *, export_root: Path | None = None, acceptance_path: Path | None = None) -> dict:
    """Inspect local preparation only; output contains no credentials or content.

    No API startup, migrations, profile reads, provider calls, ADB, or flag writes.
    Current-candidate acceptance evaluates implementation readiness. Deferred
    physical/deployment gates remain separate, even when a boolean is asserted.
    """
    checks = []

    def add(identifier, passed, summary, **evidence):
        checks.append({"id": identifier, "status": "passed" if passed else "blocked",
                       "summary": summary, "evidence": evidence})

    password_configured = bool(config.auth_password.get_secret_value())
    add("owner_auth", config.jarvis_access_mode == "personal" or password_configured,
        "Personal access uses an optional private-config password; password mode requires one.",
        access_mode=config.jarvis_access_mode, password_configured=password_configured)
    if config.jarvis_access_mode == "personal":
        from app.jarvis_tasks.personal_host import transport_check, listener_check, STATE, process_has_exited
        transport = {}
        verified = False
        try:
            transport = transport_check(config)
            host = json.loads(STATE.read_text(encoding="utf-8"))
            if (host["status"] == "Ready" and host["access_mode"] == config.jarvis_access_mode
                    and host["password_configured"] == password_configured
                    and process_has_exited(host["pid"], host["creation_stamp"]) is False):
                transport.update(listener_check(host["pid"]))
                verified = True
        except (OSError, ValueError, RuntimeError, KeyError, subprocess.SubprocessError):
            pass
        add("personal_transport", verified,
            "Personal mode requires observed owned loopback listeners and private Tailscale HTTPS with Funnel off.", **transport)
    add("session_signing", len(config.session_secret_key) >= 32 and config.session_secret_key != "dev-secret-change-me",
        "An explicitly configured session signing secret is required.")
    add("native_boundary", config.task_windows_sandbox in {"elevated", "unelevated"},
        "Native local execution additionally requires workload and folder-boundary acceptance.")

    desktop = (Path.home() / ".codex").resolve()
    workers = config.codex_profiles_path.resolve()
    background = Path(config.task_orchestration_profile)
    # The current runtime resolves this value against the backend working directory.
    background = (background if background.is_absolute() else config.backend_root / background).resolve()
    add("profile_isolation", not _overlap(workers, desktop) and not _overlap(background, desktop)
        and not _overlap(workers, background),
        "Worker/background profile roots must be separate from each other and the desktop profile; credentials are not inspected.")
    try:
        sdk_version = version("openai-codex")
    except PackageNotFoundError:
        sdk_version = None
    add("pinned_runtime_package", sdk_version == "0.155.1", "The tested runtime package must match the pinned version.",
        installed=sdk_version, expected="0.155.1", native_execution_verified=False)

    export = export_root or config.backend_root.parent / "frontend/result-export/out"
    missing = [name for name, filename in SHELL_FILES.items() if not (export / filename).is_file()]
    assets = (export / "_next/static").is_dir()
    add("result_shell", not missing and assets, "Build the production companion before starting the API.",
        missing_pages=missing, static_assets_present=assets, browser_verified=False)

    migration = Config(str(config.alembic_config_path))
    migration.set_main_option("script_location", str(config.backend_root / "alembic"))
    head = ScriptDirectory.from_config(migration).get_current_head()
    database = config.database_path
    if database is None or not database.is_file():
        add("database", False, "An existing SQLite database is required; this check never creates or migrates it.",
            expected_revision=head, exists=bool(database and database.is_file()))
    else:
        try:
            deadline = monotonic() + 2
            with closing(sqlite3.connect(database.resolve().as_uri() + "?mode=ro", uri=True, timeout=.1)) as connection:
                connection.execute("PRAGMA query_only=ON")
                connection.set_progress_handler(lambda: int(monotonic() >= deadline), 1000)
                present = {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
                revisions = [row[0] for row in connection.execute("SELECT version_num FROM alembic_version")] if "alembic_version" in present else []
                missing_tables = [table for table in TABLES if table not in present]
                foreign_keys = not connection.execute("PRAGMA foreign_key_check").fetchall()
            add("database", revisions == [head] and not missing_tables and foreign_keys,
                "Schema and foreign keys must match the current code; migration is an explicit deployment step.",
                expected_revision=head, revision_matches=revisions == [head], missing_tables=missing_tables,
                foreign_keys_valid=foreign_keys, opened_read_only=True)
        except (sqlite3.Error, OSError):
            add("database", False, "Database inspection failed or timed out; no migration was attempted.",
                expected_revision=head, opened_read_only=True)

    flags = {name: getattr(config, name) for name in ("task_pool_enabled", "task_orchestration_enabled",
        "task_orchestration_live_enabled", "task_research_enabled",
        "task_windows_sandbox", "task_command_mode")}
    flags["task_executor"] = config.task_executor
    external = [
        {"id": "custom_wake_live_provider", "status": "blocked", "owner": "agent_test_with_authorized_provider",
         "summary": "Run end-to-end wake/audio/provider acceptance; fixture and system-gesture evidence do not pass this gate."},
        {"id": "physical_oem", "status": "blocked", "owner": "deferred_physical_device",
         "summary": "Physical acoustics, protected/locked context, OEM persistence and real-phone delivery remain deferred."},
        {"id": "subscription", "status": "blocked", "owner": "account_authorization",
         "summary": "M9 requires a separately authorized private Jarvis subscription profile and shared-owner route evidence."},
        {"id": "production_opt_in", "status": "blocked", "owner": "user_direction",
         "summary": "Explicitly approve the deployment transport, device and progressive enablement; no flags are changed here."},
        {"id": "complete_backup", "status": "optional", "owner": "agent_preparation",
         "summary": "A database-only fixture restore is not a full production backup: retain context blobs, artifacts, workspaces and native profiles together after confirmed writer stop."},
    ]
    readiness = {"status": "blocked", "implementation_ready": False, "reasons": ["Current candidate M10 acceptance report missing"]}
    if acceptance_path is not None:
        from app.jarvis_tasks.acceptance import candidate_identity, evaluate
        try:
            if acceptance_path.stat().st_size > 2_000_000:
                raise ValueError("Evidence exceeds size limit")
            readiness = evaluate(json.loads(acceptance_path.read_text(encoding="utf-8")), candidate_identity())
        except (OSError, ValueError, TypeError):
            readiness = {"status": "blocked", "implementation_ready": False, "reasons": ["Acceptance evidence invalid"]}
    preparation_passed = all(c["status"] == "passed" for c in checks)
    if readiness["implementation_ready"]:
        for gate in external:
            if gate["id"] in {"custom_wake_live_provider", "subscription"}:
                gate["status"] = "passed"
                gate["evidence"] = "Current candidate M10 acceptance"
            elif gate["id"] in {"physical_oem", "production_opt_in"}:
                gate["status"] = "deferred_M11"
    return {"schema": "jarvis-rollout-check.v1", "observed_at": datetime.now(timezone.utc).isoformat(),
            "status": "passed" if preparation_passed and readiness["implementation_ready"] else "blocked",
            "implementation_readiness": readiness, "preparation_status": "passed" if preparation_passed else "blocked",
            "ready_for_enablement": False, "checks": checks, "external_gates": external,
            "current_flags": flags, "session_cookie_secure": config.session_cookie_secure,
            "provider_calls": 0, "mutations": [], "scope": "Read-only local preparation; not full rollout acceptance or authorization"}
