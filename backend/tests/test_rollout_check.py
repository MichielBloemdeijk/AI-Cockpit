"""Agent-run rollout preparation is read-only, redacted and fails closed."""
import json
from pathlib import Path
import sqlite3

from pydantic import SecretStr
import pytest

from app.config import Settings, settings
from app.jarvis_tasks.rollout_check import inspect_rollout, migration_rehearsal, rehearsal_report, write_rollout_report
from app.jarvis_tasks.sqlite_backup import snapshot_database
from app.services.result_static import SHELL_FILES


def configured(tmp_path):
    return Settings(_env_file=None, database_url=settings.database_url,
        auth_password=SecretStr("private-password"), jarvis_access_mode="password",
        session_secret_key="private-session-key-" + "x" * 32,
        codex_profiles_dir=str(tmp_path / "workers"), task_orchestration_profile=str(tmp_path / "background"),
        task_pool_enabled=False, task_orchestration_enabled=False, task_orchestration_live_enabled=False,
        task_research_enabled=False)


def export_fixture(tmp_path):
    root = tmp_path / "export"
    for filename in SHELL_FILES.values():
        file = root / filename
        file.parent.mkdir(parents=True, exist_ok=True)
        file.write_text("data-free shell", encoding="utf-8")
    (root / "_next/static").mkdir(parents=True)
    return root


def check(report, identifier):
    return next(item for item in report["checks"] if item["id"] == identifier)


def test_preparation_never_grants_rollout_and_redacts_credentials(tmp_path):
    config = configured(tmp_path)
    before = Path(settings.database_path).read_bytes()
    report = inspect_rollout(config, export_root=export_fixture(tmp_path))
    assert report["preparation_status"] == "passed", report
    assert report["status"] == "blocked" and report["ready_for_enablement"] is False
    assert report["provider_calls"] == 0 and report["mutations"] == []
    assert Path(settings.database_path).read_bytes() == before
    assert not Path(config.codex_profiles_dir).exists()  # No profile creation/auth reads.
    encoded = json.dumps(report)
    assert config.auth_password.get_secret_value() not in encoded and config.session_secret_key not in encoded
    assert str(tmp_path) not in encoded and "private-password" not in encoded
    assert "provider_budget" not in {gate["id"] for gate in report["external_gates"]}


def test_missing_database_assets_and_development_auth_fail_closed_without_creating_files(tmp_path):
    config = configured(tmp_path)
    config.database_url = f"sqlite+aiosqlite:///{(tmp_path / 'absent.db').as_posix()}"
    config.auth_password = SecretStr("")
    config.jarvis_access_mode = "password"
    config.session_secret_key = "dev-secret-change-me"
    report = inspect_rollout(config, export_root=tmp_path / "no-export")
    assert report["preparation_status"] == "blocked"
    assert all(check(report, name)["status"] == "blocked" for name in ("database", "result_shell", "owner_auth", "session_signing"))
    assert not (tmp_path / "absent.db").exists() and not (tmp_path / "no-export").exists()


@pytest.mark.parametrize("kind", ["desktop", "desktop-child", "desktop-parent", "shared"])
def test_unsafe_profile_or_tool_configuration_is_reported_not_repaired(tmp_path, kind):
    config = configured(tmp_path)
    desktop = Path.home() / ".codex"
    if kind == "desktop":
        config.codex_profiles_dir = str(desktop)
    elif kind == "desktop-child":
        config.task_orchestration_profile = str(desktop / "jarvis")
    elif kind == "desktop-parent":
        config.codex_profiles_dir = str(desktop.parent)
    elif kind == "shared":
        config.task_orchestration_profile = str(Path(config.codex_profiles_dir) / "background")
    original = config.model_dump()
    report = inspect_rollout(config, export_root=export_fixture(tmp_path))
    assert check(report, "profile_isolation")["status"] == "blocked"
    assert config.model_dump() == original


def test_stale_or_corrupt_database_is_not_migrated_or_reported_ready(tmp_path):
    config = configured(tmp_path)
    path = tmp_path / "stale.db"
    with sqlite3.connect(path) as connection:
        connection.execute("CREATE TABLE alembic_version (version_num TEXT)")
        connection.execute("INSERT INTO alembic_version VALUES ('private-stale-value')")
    config.database_url = f"sqlite+aiosqlite:///{path.as_posix()}"
    before = path.read_bytes()
    report = inspect_rollout(config, export_root=export_fixture(tmp_path))
    assert check(report, "database")["status"] == "blocked" and path.read_bytes() == before
    assert "private-stale-value" not in json.dumps(report)
    path.write_bytes(b"not-a-sqlite-database")
    assert check(inspect_rollout(config, export_root=tmp_path / "export"), "database")["status"] == "blocked"


def test_online_backup_includes_uncheckpointed_wal_without_overwriting(tmp_path):
    source, destination = tmp_path / "live.db", tmp_path / "snapshot.db"
    with sqlite3.connect(source) as connection:
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute("PRAGMA wal_autocheckpoint=0")
        connection.execute("CREATE TABLE retained (id INTEGER PRIMARY KEY, body TEXT)")
        connection.execute("INSERT INTO retained VALUES (1, 'private retained content')")
        connection.commit()
        assert Path(str(source) + "-wal").stat().st_size > 0
        proof = snapshot_database(source, destination)
        with sqlite3.connect(destination) as restored:
            assert restored.execute("SELECT * FROM retained").fetchall() == [(1, "private retained content")]
        assert "private retained content" not in json.dumps(proof)
        original = destination.read_bytes()
        with pytest.raises(FileExistsError):
            snapshot_database(source, destination)
        assert destination.read_bytes() == original
        with pytest.raises(ValueError):
            snapshot_database(source, source)
        with pytest.raises(FileNotFoundError):
            snapshot_database(tmp_path / "missing.db", tmp_path / "never-created.db")
        assert not (tmp_path / "never-created.db").exists()


def test_backup_refuses_foreign_key_damage_and_times_out_busy_writer(tmp_path):
    source = tmp_path / "damaged.db"
    with sqlite3.connect(source) as connection:
        connection.execute("CREATE TABLE parent (id INTEGER PRIMARY KEY)")
        connection.execute("CREATE TABLE child (parent_id INTEGER REFERENCES parent(id))")
        connection.execute("INSERT INTO child VALUES (404)")
    with pytest.raises(ValueError, match="foreign-key"):
        snapshot_database(source, tmp_path / "damaged-snapshot.db")
    # SQLite busy backup retries must not become an unbounded agent wait.
    with sqlite3.connect(source) as writer:
        writer.execute("BEGIN EXCLUSIVE")
        with pytest.raises(TimeoutError):
            snapshot_database(source, tmp_path / "busy-snapshot.db", timeout_seconds=.02)
        writer.rollback()


def test_backup_refuses_stale_sidecars_and_invalid_deadlines(tmp_path):
    source = tmp_path / "source.db"
    with sqlite3.connect(source) as connection:
        connection.execute("CREATE TABLE retained (id INTEGER PRIMARY KEY)")
    destination = tmp_path / "sidecar.db"
    Path(str(destination) + "-wal").write_bytes(b"existing SQLite sidecar")
    with pytest.raises(FileExistsError, match="sidecars"):
        snapshot_database(source, destination)
    assert not destination.exists()
    for timeout in (0, -1, float("nan"), float("inf")):
        with pytest.raises(ValueError):
            snapshot_database(source, tmp_path / "invalid.db", timeout_seconds=timeout)
    assert not (tmp_path / "invalid.db").exists()


def test_rehearsal_rejects_missing_empty_skipped_and_failed_evidence(tmp_path):
    path = tmp_path / "checks.xml"
    assert rehearsal_report(0, path)["status"] == "failed"
    for xml in ("<testsuites/>", '<testsuites><testcase name="case"><skipped/></testcase></testsuites>',
                '<testsuites><testcase name="case"><failure>private diagnostics</failure></testcase></testsuites>'):
        path.write_text(xml, encoding="utf-8")
        report = rehearsal_report(0, path)
        assert report["status"] == "failed" and "private diagnostics" not in json.dumps(report)
    path.write_text('<testsuites><testcase name="case"/></testsuites>', encoding="utf-8")
    assert rehearsal_report(1, path)["status"] == "failed"
    assert rehearsal_report(0, path)["status"] == "passed"
    assert rehearsal_report(0, path)["ready_for_enablement"] is False


def test_migration_rehearsal_removes_clone_preserves_source_and_never_enables(tmp_path):
    config = configured(tmp_path)
    source = config.database_path
    before = source.read_bytes()
    report = migration_rehearsal(config)
    assert report["status"] == "passed", report
    assert report["clone_removed"] is True and report["complete_backup"] is False
    assert report["ready_for_enablement"] is False and report["production_mutations"] == []
    assert source.read_bytes() == before
    assert str(source) not in json.dumps(report)
    assert all(item["unchanged"] for item in report["retained_tables"])
    config.database_url = f"sqlite+aiosqlite:///{(tmp_path / 'absent-migration.db').as_posix()}"
    assert migration_rehearsal(config)["failure"] == "sqlite_source_unavailable"
    assert not (tmp_path / "absent-migration.db").exists()


def test_report_writer_preserves_unrelated_files_and_only_replaces_its_own_schema(tmp_path):
    report = {"schema": "jarvis-rollout-check.v1", "status": "blocked"}
    path = tmp_path / "report.json"
    write_rollout_report(path, report)
    write_rollout_report(path, report | {"preparation_status": "passed"})
    assert json.loads(path.read_text(encoding="utf-8"))["preparation_status"] == "passed"
    original = path.read_bytes()
    with pytest.raises(ValueError):
        write_rollout_report(path, report | {"schema": "another-schema"})
    assert path.read_bytes() == original
    private = tmp_path / "auth.json"
    private.write_text('{"private": "not-a-report"}', encoding="utf-8")
    with pytest.raises(ValueError):
        write_rollout_report(private, report)
    assert private.read_text(encoding="utf-8") == '{"private": "not-a-report"}'
    with pytest.raises(ValueError):
        write_rollout_report(tmp_path / "database.db", report)
    assert not (tmp_path / "database.db").exists()
