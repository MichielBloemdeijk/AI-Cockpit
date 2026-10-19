"""Candidate-bound M6–M10 acceptance. Evidence never authorizes deployment."""
from __future__ import annotations

from datetime import datetime, timezone, timedelta
from hashlib import sha256, file_digest
from importlib.metadata import version
import json
import math
import os
import platform
from pathlib import Path
import shutil
import signal
import stat
import subprocess
import sys
from time import monotonic
from xml.etree import ElementTree

from app.config import settings

SCHEMA = "jarvis-task-acceptance.v1"
# Full audio evidence retains correlated observations for every required case.
# Its generated report is larger than a single assertion or targeted pilot.
MAX_EVIDENCE_BYTES = 8_000_000
ROOT = Path(__file__).resolve().parents[3]
REQUIRED = {
    "M6": {"backend", "coding_provider", "process_tree"},
    "M7": {"backend", "voice_provider"},
    "M8": {"android", "audio_corpus", "ready_latency", "cold_latency", "fresh_screen", "capture_performance", "capture_privacy", "mobile_result"},
    "M9": {"backend", "subscription_provider"},
    "M10": {"backend", "frontend_types", "frontend_tests", "frontend_build", "result_shell", "android",
        "browser", "coding_provider", "process_tree", "voice_provider", "audio_corpus",
        "ready_latency", "cold_latency", "fresh_screen", "capture_performance", "capture_privacy", "mobile_result", "subscription_provider", "migration"},
}


def accepted_exceptions(gate: str) -> dict:
    """Only the explicitly accepted M8 cohorts can be excepted, never safety checks."""
    policy = json.loads(Path(__file__).with_name("acceptance-profiles.json").read_text())["accepted_exceptions"]
    allowed = {"audio_corpus", "cold_latency", "fresh_screen", "capture_performance"}
    if (policy["decision"] != "m8-accepted-with-caveats-2026-10-07"
            or set(policy["gates"]) != {"M8", "M10"}
            or set(policy["assertions"]) != allowed
            or any(not isinstance(reason, str) or not reason.strip() for reason in policy["assertions"].values())):
        raise ValueError("Invalid accepted M8 exceptions")
    return {identifier: {"decision": policy["decision"], "reason": reason}
        for identifier, reason in policy["assertions"].items()} if gate in policy["gates"] else {}


def candidate_identity(root: Path = ROOT) -> dict:
    """Hash source including dirty/untracked files, never profiles, .env or data."""
    source_roots = ("backend/app", "backend/alembic", "backend/tests", "backend/workflows", "frontend/app", "frontend/components",
        "frontend/lib", "frontend/scripts", "frontend/e2e", "frontend/result-export", "frontend/public",
        "android/app/src", "android/screen-fixture/src", "android/scripts", "scripts")
    files = set()
    excluded = {"node_modules", ".next", "out", "build", "__pycache__"}
    def linked(path):
        return path.is_symlink() or bool(getattr(path.lstat(), "st_file_attributes", 0) & stat.FILE_ATTRIBUTE_REPARSE_POINT)
    for relative in source_roots:
        folder = root / relative
        if not folder.is_dir() or linked(folder):
            continue
        # Prune before descending: generated trees can dwarf the source tree.
        # A package named data inside an explicit source root is real source.
        for directory, children, names in os.walk(folder, followlinks=False):
            parent = Path(directory)
            children[:] = [name for name in children if name not in excluded
                and not linked(parent / name)]
            files.update(parent / name for name in names
                if Path(name).suffix not in {".pyc", ".log"}
                and not (parent / name).is_symlink() and (parent / name).is_file())
    for relative in ("Jarvis.cmd", "backend/pyproject.toml", "backend/uv.lock", "frontend/package.json", "frontend/package-lock.json",
        "frontend/tsconfig.json", "frontend/tsconfig.live-smoke.json", "frontend/test-globals.d.ts",
        "frontend/next.config.ts", "frontend/vitest.config.mts", "frontend/playwright.live.config.ts",
        "frontend/postcss.config.mjs", "frontend/eslint.config.mjs", "android/gradle/libs.versions.toml",
        "android/build.gradle.kts", "android/settings.gradle.kts", "android/gradle.properties",
        "android/app/build.gradle.kts", "android/screen-fixture/build.gradle.kts", "android/gradle/wrapper/gradle-wrapper.properties"):
        if (root / relative).is_file() and not linked(root / relative):
            files.add(root / relative)
    digest = sha256()
    for path in sorted(files, key=lambda p: p.relative_to(root).as_posix()):
        with path.open("rb") as source:
            digest.update(path.relative_to(root).as_posix().encode() + b"\0" + file_digest(source, "sha256").digest())
    revision = subprocess.run(["git", "rev-parse", "HEAD"], cwd=root, capture_output=True, text=True, check=True).stdout.strip()
    configuration = {key: getattr(settings, key) for key in (
        "jarvis_access_mode", "jarvis_public_url", "session_cookie_secure", "task_executor", "task_workflow_allowlist", "codex_task_model", "codex_task_reasoning_effort",
        "task_project_roots", "codex_subscription_enabled",
        "task_execution_model_allowlist", "codex_subscription_profile", "openai_live_base_url", "openai_live_model",
        "openai_live_responses_model", "openai_live_reasoning_effort",
        "jarvis_conversation_observer_mode",
        "openai_live_diagnostics_dir", "openai_live_reasoning_summary", "task_orchestration_model",
        "task_orchestration_effort", "worker_max_busy", "worker_standby_count", "task_pool_enabled",
        "task_orchestration_enabled", "task_orchestration_live_enabled", "task_research_enabled",
        "task_orchestration_profile", "task_command_mode", "task_windows_sandbox",
        "codex_profiles_dir", "task_workspaces_dir", "task_context_blobs_dir", "conversation_workspaces_dir")}
    configuration["worker_tool_policy"] = worker_policy_identity()
    sdk = version("openai-codex")
    settings_digest = sha256(json.dumps(configuration, sort_keys=True).encode()).hexdigest()
    identity = {"revision": revision, "source_digest": digest.hexdigest(), "configuration_digest": settings_digest,
        "configuration": configuration, "sdk": sdk, "python": sys.version.split()[0], "platform": platform.platform(), "source_file_count": len(files)}
    identity["id"] = sha256(json.dumps(identity, sort_keys=True).encode()).hexdigest()
    return identity


def worker_policy_identity() -> dict:
    """Bind evidence to persisted grants and approved skill source, not credentials."""
    import sqlite3
    from app.services.worker_tool_policy import WorkerPolicy
    policy, revision, permissions = WorkerPolicy().model_dump(), 0, []
    path = settings.database_path
    if path and path.is_file():
        with sqlite3.connect(path.as_uri() + "?mode=ro", uri=True) as connection:
            tables = {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            if "worker_tool_policy" in tables:
                row = connection.execute("SELECT revision, configuration_json FROM worker_tool_policy WHERE id='worker'").fetchone()
                if row:
                    revision, policy = row[0], WorkerPolicy.model_validate(json.loads(row[1])).model_dump()
            if "connector_tool_permissions" in tables:
                permissions = [list(row) for row in connection.execute(
                    "SELECT identity, mode, revision FROM connector_tool_permissions ORDER BY identity")]
    skill_sources = {}
    for name in policy["approved_skills"]:
        folder = Path(name).parent
        digest = sha256()
        for directory, children, names in os.walk(folder, followlinks=False):
            parent = Path(directory)
            children[:] = sorted(child for child in children if child not in {"__pycache__", "node_modules", ".git"}
                and not (parent / child).is_symlink())
            for child in sorted(names):
                source = parent / child
                if source.is_file() and not source.is_symlink():
                    with source.open("rb") as stream:
                        digest.update(source.relative_to(folder).as_posix().encode() + b"\0" + file_digest(stream, "sha256").digest())
        skill_sources[name] = digest.hexdigest()
    return {"revision": revision, "policy": policy, "permissions": permissions, "skill_sources": skill_sources}



def resolve_test_configuration(profile: str, *, paths: dict[str, str] | None = None, browser_origin: str | None = None) -> dict:
    """One executable manifest defines allowed gate-specific configurations."""
    manifest = json.loads(Path(__file__).with_name("acceptance-profiles.json").read_text())
    definition = manifest["profiles"][profile]
    resolved = dict(definition["overrides"])
    if definition.get("browser_origin"):
        from urllib.parse import urlparse
        parsed = urlparse(browser_origin or "")
        if parsed.scheme != "http" or parsed.hostname != "10.0.2.2" or not parsed.port or parsed.path or parsed.query or parsed.fragment or parsed.username:
            raise ValueError("Emulator browser origin must be its explicit host-loopback HTTP endpoint")
        resolved["jarvis_public_url"] = browser_origin
    elif browser_origin is not None:
        raise ValueError("This test profile does not declare a browser origin")
    paths = paths or {}
    if set(paths) != set(definition["private_paths"]):
        raise ValueError("Gate test configuration requires exact declared private paths")
    for key, value in paths.items():
        path = Path(value).resolve()
        # Test directories are inherited, isolated, and never the user profile.
        import tempfile
        permitted = [ROOT / "data", ROOT / "backend/data", Path(tempfile.gettempdir()) / "jarvis-test-execution"]
        if not any(path.is_relative_to(parent.resolve()) and path != parent.resolve() for parent in permitted):
            raise ValueError("Gate private path is outside the isolated execution roots")
        resolved[key] = str(path)
    if definition.get("workflow_definitions"):
        from app.services.task_workflows import load_workflow
        resolved["task_workflow_allowlist"] = ",".join(f"{w.id}@{w.version}:{w.digest}" for w in
            (load_workflow(name) for name in definition["workflow_definitions"]))
    return {"profile": profile, "settings": resolved,
        "manifest_sha256": sha256(Path(__file__).with_name("acceptance-profiles.json").read_bytes()).hexdigest()}


def candidate_matches(observed: dict, expected: dict, *, identifier: str, configuration: dict | None = None) -> bool:
    if observed == expected:
        return True
    if not isinstance(configuration, dict) or not isinstance(observed, dict) or not isinstance(expected, dict):
        return False
    try:
        manifest = json.loads(Path(__file__).with_name("acceptance-profiles.json").read_text())
        profile = configuration["profile"]
        definition = manifest["profiles"][profile]
        if identifier not in definition["assertions"]:
            return False
        resolved = resolve_test_configuration(profile, paths={key: configuration["settings"][key] for key in definition["private_paths"]},
            browser_origin=configuration["settings"].get("jarvis_public_url") if definition.get("browser_origin") else None)
        if configuration != resolved:
            return False
        keys = set(observed) - {"id", "configuration", "configuration_digest"}
        if keys != set(expected) - {"id", "configuration", "configuration_digest"} or any(observed[key] != expected[key] for key in keys):
            return False
        actual = dict(expected["configuration"])
        actual.update(resolved["settings"])
        if observed["configuration"] != actual:
            return False
        digest = sha256(json.dumps(actual, sort_keys=True).encode()).hexdigest()
        if observed["configuration_digest"] != digest:
            return False
        body = {key: value for key, value in observed.items() if key != "id"}
        return observed["id"] == sha256(json.dumps(body, sort_keys=True).encode()).hexdigest()
    except (KeyError, TypeError, ValueError):
        return False

def database_identity(path: Path | None) -> str | None:
    """Bind a local acceptance reader to the backend's database without exposing its path."""
    return None if path is None else sha256(os.path.normcase(str(path.resolve())).encode()).hexdigest()


def evaluate(report: dict, candidate: dict, *, gate: str = "M10", now: datetime | None = None) -> dict:
    now = now or datetime.now(timezone.utc)
    reasons = []
    if report.get("schema") != SCHEMA or report.get("candidate") != candidate or report.get("gate") != gate:
        reasons.append("Evidence schema, gate or candidate mismatch")
    try:
        observed = datetime.fromisoformat(report["finished_at"])
        if observed.tzinfo is None or observed > now + timedelta(seconds=5) or now - observed > timedelta(hours=24):
            reasons.append("Evidence is stale or has an invalid clock")
    except (ValueError, KeyError, TypeError):
        reasons.append("Evidence timestamp missing")
    checks = report.get("checks", [])
    if not isinstance(checks, list):
        checks = []
        reasons.append("Malformed assertions")
    identifiers = [check.get("id") for check in checks if isinstance(check, dict) and isinstance(check.get("id"), str)]
    if len(identifiers) != len(checks) or len(set(identifiers)) != len(identifiers):
        reasons.append("Duplicate or malformed assertion IDs")
    by_id = {check["id"]: check for check in checks if isinstance(check, dict) and isinstance(check.get("id"), str)}
    exceptions = accepted_exceptions(gate)
    for identifier in sorted(REQUIRED[gate]):
        check = by_id.get(identifier)
        if identifier in exceptions:
            if (check is None or check.get("status") != "accepted_with_caveats"
                    or check.get("evidence") != exceptions[identifier] or check.get("skipped", 0)):
                reasons.append(f"Accepted exception {identifier} lacks its explicit decision record")
            continue
        if check is None or check.get("status") != "passed" or check.get("skipped", 0):
            reasons.append(f"Required assertion {identifier} is missing, skipped or not passed")
        elif not check.get("evidence"):
            reasons.append(f"Required assertion {identifier} lacks evidence")
        elif error := validate_observation(identifier, check["evidence"]):
            reasons.append(f"Required assertion {identifier}: {error}")
    return {"status": "passed" if not reasons else "blocked", "reasons": reasons,
        "accepted_exceptions": sorted(exceptions),
        "acceptance_basis": "user_accepted_caveats" if exceptions else "all_required_assertions_passed",
        "implementation_ready": not reasons, "ready_for_enablement": False}


def validate_observation(identifier: str, evidence: dict) -> str | None:
    """Passing markers alone cannot satisfy a live acceptance assertion."""
    if not isinstance(evidence, dict):
        return "Malformed evidence"
    if identifier in {"ready_latency", "cold_latency"}:
        rows = evidence.get("samples", [])
        ready = identifier == "ready_latency"
        if ready and evidence.get("policy") != "single_final_under_4500_ms":
            return "Final admission timing policy missing"
        if not isinstance(rows, list) or (len(rows) != 1 if ready else len(rows) < 5) or evidence.get("clock") != "android_elapsed_realtime_nanos":
            return "Insufficient samples or incompatible clocks"
        values, sessions = [], set()
        try:
            for row in rows:
                if row["status"] != "passed" or row["path"] != ("ready" if ready else "cold"):
                    return "A required sample failed or has the wrong startup path"
                if not all(row[key] for key in ("task_id", "turn_id", "execution_run_id", "sample_id", "session_id")):
                    return "Durable admission identities missing"
                duration = (int(row["client_received_ns"]) - int(row["command_last_sample_ns"])) / 1_000_000
                if duration < 0 or int(row["command_last_sample_ns"]) <= 0:
                    return "Invalid timing interval"
                values.append(duration)
                sessions.add(row["session_id"])
            if len({row["sample_id"] for row in rows}) != len(rows):
                return "Duplicate latency samples"
            if not ready and len(sessions) < 5:
                return "Cold samples must use independently initialized sessions"
            if ready and values[0] >= 4500:
                return "Final ready admission must be below 4500 ms"
        except (KeyError, TypeError, ValueError):
            return "Incomplete timing observations"
    elif identifier == "audio_corpus":
        rows = evidence.get("cases", [])
        if not isinstance(rows, list) or any(not isinstance(row, dict) for row in rows):
            return "Malformed corpus observations"
        corpus = ROOT / "android/scripts/audio-corpus-v1.json"
        cases = json.loads(corpus.read_text(encoding="utf-8"))["cases"]
        required_cases = {case["id"] for case in cases}
        passed_cases = {row.get("case_id") for row in rows if row.get("status") == "passed"}
        if not required_cases <= passed_cases or any(row.get("status") != "passed" for row in rows):
            return "Fixed corpus contains missing, blocked or failed cases"
        categories = {row.get("category") for row in rows if row.get("status") == "passed"}
        if not {"general", "screen_workflow", "ambiguity", "nonmatch", "multiple_outcomes", "control", "question", "followup", "fresh_screen"} <= categories:
            return "Required corpus outcomes missing"
        if not evidence.get("corpus_sha256") or not evidence.get("real_rtp_bytes_sent", 0) or not evidence.get("playback_observed"):
            return "Corpus identity, real RTP or assistant playback missing"
    elif identifier == "coding_provider":
        if not (evidence.get("supervised") and evidence.get("journaled") and evidence.get("command_boundary_passed")
            and evidence.get("execution_mode") in {"native", "trusted"}
            and evidence.get("failing_test_exit", 0) != 0 and evidence.get("passing_test_exit") == 0
            and evidence.get("restart_same_task_continuation")):
            return "Supervised coding failure/repair/restart proof incomplete"
    elif identifier == "process_tree":
        if not (evidence.get("exact_pid_creation_stamp") and evidence.get("descendants_stopped")
            and evidence.get("writer_growth_after_stop") == 0):
            return "Exact descendant stop proof missing"
    elif identifier == "voice_provider":
        if evidence.get("adapter") != "openai_live" or evidence.get("requested_tier") != "priority":
            return "Real delegated priority route not observed"
        if evidence.get("actual_tier") not in {"priority", "default"} or (evidence.get("actual_tier") == "default"
            and (evidence.get("fallback_reason") not in {"unsupported_service_tier", "service_tier_not_supported"} or evidence.get("fallback_count") != 1)):
            return "Provider tier/fallback evidence incomplete"
    elif identifier == "subscription_provider":
        if not (evidence.get("account_type") == "chatgpt" and evidence.get("separate_profile")
            and evidence.get("shared_owner_competition") and evidence.get("same_task_safe_cutover")):
            return "Private subscription ownership and cutover evidence incomplete"
    elif identifier == "fresh_screen":
        if not (evidence.get("api_level", 0) >= 37 and evidence.get("custom_wake")
            and evidence.get("initial_screen_A") and evidence.get("fresh_screen_B")
            and evidence.get("clarification_before_B") and evidence.get("admitted_capture") == "B"):
            return "API37 real custom-wake A → clarification → fresh B proof incomplete"
    elif identifier == "capture_privacy":
        required = {"wake_policy_observed", "screenshot_independent_of_structure_consent", "visible_assistant_capture",
            "underlying_screen_a", "underlying_screen_b", "fresh_keeps_activation_and_session", "one_live_session",
            "protected_rejects_without_task", "android_disabled_rejects_without_task", "denied_rejects_without_task", "locked_rejects_without_task"}
        native = evidence.get("native_checks", {})
        contracts = evidence.get("contracts", {})
        if (evidence.get("api_level") != 37 or evidence.get("provider_calls") != 0
            or not all(native.get(key) is True for key in required)
            or contracts.get("exit_code") != 0 or not contracts.get("test_count") or contracts.get("skipped") != 0):
            return "Native privacy or expiry/replacement/upload contract evidence incomplete"
    elif identifier == "mobile_result":
        required = ("custom_wake", "real_notification_delivered", "notification_action_tapped", "reopened_result",
            "no_extra_run_on_reopen", "ui_followup", "same_task_continuation", "both_screen_roles")
        if (evidence.get("adapter") != "openai_live" or evidence.get("workflow") != "research_screen@1"
            or not all(evidence.get(key) is True for key in required) or not evidence.get("source_count")
            or not all(evidence.get(key) for key in ("task_id", "initial_run_id", "followup_run_id", "public_id"))
            or evidence.get("initial_run_id") == evidence.get("followup_run_id")):
            return "Real custom-wake result/notification/reopen/UI follow-up proof incomplete"
    elif identifier == "capture_performance":
        limits = {"callback_sync_ms": 5, "cached_upload_start_ms": 500,
            "fresh_request_callback_ms": 2000, "initial_callback_after_speech_ms": 2000}
        try:
            if evidence.get("clock") != "android_elapsed_realtime_nanos" or not evidence.get("baseline_identity"):
                return "Android clock and measured Live-ready baseline missing"
            for field, limit in limits.items():
                values = evidence[field]
                if not isinstance(values, list) or len(values) < 20 or any(type(v) not in (float, int) or not math.isfinite(v)
                    or (v < 0 and field != "live_ready_overhead_ms") for v in values):
                    return "Insufficient capture performance samples"
                measured = sorted(values)[math.ceil(.95 * len(values)) - 1]
                if measured > limit or (measured == limit and field not in {"live_ready_overhead_ms", "cached_upload_start_ms"}):
                    return f"Capture p95 failed: {field}"
            startup = []
            for key in ("baseline_live_ready_ms", "capture_live_ready_ms"):
                values = evidence[key]
                if len(values) < 20 or any(type(v) not in (int, float) or not math.isfinite(v) or v < 0 for v in values):
                    return "Insufficient controlled startup samples"
                startup.append(sorted(values)[math.ceil(.95 * len(values)) - 1])
            delta = startup[1] - startup[0]
            if evidence.get("live_ready_p95_difference_ms") != delta or delta > 150:
                return "Capture startup p95 difference exceeds 150 ms or is incorrectly measured"
            if evidence.get("frame_window_ns") != 20_000_000_000 or not evidence.get("normalized_frame_rates"):
                return "Equal normalized frame observation windows missing"
            rates = evidence["normalized_frame_rates"]
            frame_p95 = []
            for group in ("baseline", "activation", "fresh"):
                values = rates[group]
                if (not isinstance(values, list) or len(values) < 20
                        or any(type(v) not in (int, float) or not math.isfinite(v) or not 0 <= v <= 1 for v in values)):
                    return "Insufficient normalized frame observations"
                frame_p95.append(sorted(values)[math.ceil(.95 * len(values)) - 1])
            if max(frame_p95[1:]) > frame_p95[0]:
                return "Capture-induced frame regression observed"
            if evidence.get("audio_underruns") != 0 or evidence.get("anrs") != 0 or evidence.get("overlay_jank_observed") is not False:
                return "Audio or assistant-window regression observations missing"
        except (KeyError, TypeError):
            return "Capture performance observations incomplete"
    return None


def load_assertion(path: Path, candidate: dict, *, gate: str, identifier: str) -> dict:
    """Load one fresh executable observation without waiving the other gates."""
    if path.is_symlink() or path.stat().st_size > MAX_EVIDENCE_BYTES:
        raise ValueError("Evidence must be a bounded regular report")
    external = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(external, dict):
        raise ValueError("Malformed report")
    manifest = json.loads(Path(__file__).with_name("acceptance-profiles.json").read_text())
    reusable = manifest.get("assertion_reuse", {}).get(identifier, [])
    gate_matches = external.get("gate") == gate or (gate in reusable and external.get("gate") in reusable)
    if external.get("schema") != SCHEMA or not candidate_matches(external.get("candidate"), candidate, identifier=identifier,
        configuration=external.get("test_configuration")) or not gate_matches:
        raise ValueError("Evidence schema, gate or candidate mismatch")
    observed = datetime.fromisoformat(external["finished_at"])
    now = datetime.now(timezone.utc)
    if observed.tzinfo is None or observed > now + timedelta(seconds=5) or now - observed > timedelta(hours=24):
        raise ValueError("Stale evidence")
    rows = external.get("checks")
    if not isinstance(rows, list) or any(not isinstance(row, dict) or not isinstance(row.get("id"), str) for row in rows):
        raise ValueError("Malformed assertions")
    if len({row["id"] for row in rows}) != len(rows):
        raise ValueError("Duplicate assertions")
    matching = next(row for row in rows if row["id"] == identifier)
    if (matching.get("status") != "passed" or matching.get("skipped", 0) or not matching.get("evidence")
            or validate_observation(identifier, matching["evidence"])):
        raise ValueError("Required observation did not pass")
    return {**matching, "evidence": {**matching["evidence"], "source": str(path),
        "source_sha256": sha256(path.read_bytes()).hexdigest()}}


def _command(identifier, command, cwd, output: Path, *, junit: Path | None = None,
             junit_glob: str | None = None, env: dict | None = None, timeout: float = 1200) -> dict:
    started = monotonic()
    log = output / f"{identifier}.log"
    try:
        with log.open("w", encoding="utf-8") as stream:
            execution = command
            if os.name == "nt":
                execution = [sys.executable, str(Path(__file__).with_name("acceptance_command_host.py")),
                    "--parent-pid", str(os.getpid()), "--", *command]
            environment = {**os.environ, "PYTEST_ADDOPTS": "", **(env or {})}
            environment["PYTHONPATH"] = str(ROOT / "backend") + os.pathsep + environment.get("PYTHONPATH", "")
            process = subprocess.Popen(execution, cwd=cwd, stdout=stream, stderr=subprocess.STDOUT,
                env=environment, text=True, start_new_session=os.name != "nt",
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            try:
                process.wait(timeout=timeout)
            finally:
                if os.name == "nt":
                    if process.poll() is None:
                        process.kill()
                else:
                    try:
                        os.killpg(process.pid, signal.SIGKILL)
                    except ProcessLookupError:
                        pass
                process.wait(timeout=10)
        paths = [junit] if junit and junit.exists() else []
        if junit_glob:
            for index, source in enumerate(Path(cwd).glob(junit_glob)):
                copy = output / f"{identifier}-{index}.xml"
                shutil.copyfile(source, copy)
                paths.append(copy)
        checks = [case for path in paths for case in ElementTree.parse(path).getroot().iter("testcase")]
        skipped = sum(case.find("skipped") is not None for case in checks)
        failed = sum(case.find("failure") is not None or case.find("error") is not None for case in checks)
        passed = process.returncode == 0 and not skipped and not failed and (bool(checks) if junit or junit_glob else True)
        return {"id": identifier, "status": "passed" if passed else "failed" if process.returncode or failed else "blocked",
            "skipped": skipped, "duration_seconds": round(monotonic() - started, 3),
            "evidence": {"command": command, "exit_code": process.returncode, "test_count": len(checks),
                "log": str(log), "log_sha256": sha256(log.read_bytes()).hexdigest()}}
    except (OSError, subprocess.TimeoutExpired, ElementTree.ParseError) as exc:
        return {"id": identifier, "status": "failed", "evidence": {"failure": type(exc).__name__, "log": str(log)}}


def run_acceptance(*, gate: str, output: Path, evidence: dict[str, Path], live: bool = False,
                   emulator: str | None = None, audio_dir: Path | None = None, database: Path | None = None,
                   backend_url: str | None = None, emulator_configuration: dict | None = None) -> dict:
    exceptions = accepted_exceptions(gate)
    required = REQUIRED[gate] - exceptions.keys()
    if set(evidence) - required:
        raise ValueError("Unknown or irrelevant evidence assertion")
    if output.exists() and any(output.iterdir()):
        raise ValueError("Acceptance requires a fresh report directory")
    output.mkdir(parents=True, exist_ok=True)
    candidate = candidate_identity()
    # Reject stale supplied evidence before starting any paid stage.
    for identifier, path in evidence.items():
        load_assertion(path.resolve(), candidate, gate=gate, identifier=identifier)
    checks = [{"id": identifier, "status": "accepted_with_caveats", "evidence": proof}
        for identifier, proof in sorted(exceptions.items())]
    execution_failures = set()
    backend = ROOT / "backend"
    provider_env = {"JARVIS_LIVE_CODEX": "1" if live else "0", "JARVIS_M4_LIVE": "1" if live else "0",
        "RUN_M5_BROWSER": "1" if live else "0", "JARVIS_LIVE_M6": "0"}
    provider_env.update(JARVIS_ACCEPTANCE_GATE=gate)
    # The same production seams used by standalone emulator acceptance run here.
    # The caller selects an already paired emulator and isolated backend; no opt-in is inferred.
    if emulator or audio_dir or database or backend_url:
        if gate not in {"M8", "M10"}:
            raise ValueError("Emulator audio is required only by M8/M10")
        if not (live and emulator and audio_dir):
            raise ValueError("Emulator acceptance requires --live, --emulator and --audio-dir")
        missing_audio = ({"audio_corpus", "ready_latency", "cold_latency", "fresh_screen", "capture_performance"} & required) - set(evidence)
        if missing_audio and not (database and backend_url):
            raise ValueError("Unobserved audio/capture-performance assertions require a prepared --database and --backend-url")
        if not emulator.startswith("emulator-"):
            raise ValueError("Autonomous acceptance requires a prepared emulator")
        emulator_env = {}
        if emulator_configuration is not None:
            manifest = json.loads(Path(__file__).with_name("acceptance-profiles.json").read_text())
            profile = emulator_configuration["profile"]
            if profile != "emulator":
                raise ValueError("Emulator acceptance requires the declared managed configuration")
            paths = {key: emulator_configuration["settings"][key] for key in manifest["profiles"][profile]["private_paths"]}
            if resolve_test_configuration(profile, paths=paths, browser_origin=emulator_configuration["settings"].get("jarvis_public_url")) != emulator_configuration:
                raise ValueError("Emulator configuration differs from its shared manifest")
            emulator_env = {key.upper(): str(value).lower() if isinstance(value, bool) else str(value)
                for key, value in emulator_configuration["settings"].items()}
        def bind_emulator_report(path):
            if emulator_configuration and path.exists():
                report = json.loads(path.read_text(encoding="utf-8"))
                report["test_configuration"] = emulator_configuration
                path.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
        audio_output = output / "audio"
        audio_assertions = {"audio_corpus", "ready_latency", "cold_latency", "fresh_screen"} & required
        audio_passed = True
        if not audio_assertions <= set(evidence):
            # Accepted bulk cohorts must not trigger another full paid corpus.
            audio_mode = ["--prepare-scenarios"] if "audio_corpus" in required else ["--pilot", "--case", "ready07"]
            executed_audio = _command("audio_execution", [sys.executable, str(ROOT / "android/scripts/verify-audio-corpus.py"),
            "--live", "--serial", emulator, "--render-dir", str(audio_dir.resolve()),
            "--database", str(database.resolve()), "--backend-url", backend_url,
            "--gate", gate, "--report", str(audio_output), *audio_mode], backend, output, env=emulator_env)
            bind_emulator_report(audio_output / "acceptance.json")
            audio_passed = executed_audio["status"] == "passed"
        for identifier in {"audio_corpus", "ready_latency", "cold_latency", "fresh_screen", "voice_provider"} & required:
            if identifier not in evidence:
                evidence[identifier] = audio_output / "acceptance.json"
        if "capture_performance" in required and "capture_performance" not in evidence and audio_passed:
            capture_output = output / "capture-performance"
            _command("capture_execution", [sys.executable, str(ROOT / "android/scripts/verify-audio-corpus.py"),
                "--live", "--serial", emulator, "--render-dir", str(audio_dir.resolve()),
                "--database", str(database.resolve()), "--backend-url", backend_url,
                "--gate", gate, "--report", str(capture_output), "--capture-performance"], backend, output, env=emulator_env, timeout=3600)
            bind_emulator_report(capture_output / "acceptance.json")
            evidence.setdefault("capture_performance", capture_output / "acceptance.json")
    if live and "coding_provider" in required and "coding_provider" not in evidence:
        path = output / "coding-provider.json"
        executed = _command("coding_execution", [sys.executable, "-m", "pytest", "tests/test_m6_coding_live.py", "-q", "--tb=short",
            f"--junitxml={output / 'coding_execution.xml'}"], backend, output, junit=output / "coding_execution.xml",
            env={**provider_env, "JARVIS_LIVE_M6": "1", "JARVIS_M6_REPORT": str(path)})
        if executed["status"] == "passed":
            evidence["coding_provider"] = path
        else:
            checks.append({**executed, "id": "coding_provider"})
            execution_failures.add("coding_provider")
    if live and "subscription_provider" in required and "subscription_provider" not in evidence:
        path = output / "subscription-provider.json"
        executed = _command("subscription_execution", [sys.executable, "-m", "pytest", "tests/test_subscription_provider_live.py",
            "-q", "--tb=short", f"--junitxml={output / 'subscription_execution.xml'}"], backend, output,
            junit=output / "subscription_execution.xml", env={**provider_env, "JARVIS_SUBSCRIPTION_LIVE": "1", "JARVIS_SUBSCRIPTION_REPORT": str(path)})
        if executed["status"] == "passed":
            evidence["subscription_provider"] = path
        else:
            checks.append({**executed, "id": "subscription_provider"})
            execution_failures.add("subscription_provider")
    if "process_tree" in required and "process_tree" not in evidence:
        path = output / "process-tree.json"
        executed = _command("process_tree_execution", [sys.executable, "-m", "pytest", "tests/test_windows_job.py", "-q", "--tb=short",
            f"--junitxml={output / 'process_tree_execution.xml'}"], backend, output,
            junit=output / "process_tree_execution.xml", env={"JARVIS_PROCESS_TREE_REPORT": str(path), "JARVIS_ACCEPTANCE_GATE": gate})
        if executed["status"] == "passed":
            evidence["process_tree"] = path
        else:
            checks.append({**executed, "id": "process_tree"})
            execution_failures.add("process_tree")
    manifest = json.loads(Path(__file__).with_name("acceptance-profiles.json").read_text())
    backend_selection = manifest["backend_suites"][gate]
    commands = {
        "backend": ([sys.executable, "-m", "pytest", "tests", "--ignore=tests/test_m6_coding_live.py", "--ignore=tests/test_subscription_provider_live.py", "-m", backend_selection, "-q", "--tb=short", f"--junitxml={output / 'backend.xml'}"], backend),
        "frontend_types": ([shutil.which("npm.cmd") or "npm", "run", "typecheck"], ROOT / "frontend"),
        "frontend_tests": ([shutil.which("npm.cmd") or "npm", "test", "--", "--reporter=junit", f"--outputFile={output / 'frontend_tests.xml'}"], ROOT / "frontend"),
        "frontend_build": ([shutil.which("npm.cmd") or "npm", "run", "build"], ROOT / "frontend"),
        "result_shell": ([shutil.which("npm.cmd") or "npm", "run", "build:results"], ROOT / "frontend"),
        "android": ([str(ROOT / "android/gradlew.bat") if os.name == "nt" else "./gradlew", "testDebugUnitTest", "assembleDebug", "--rerun-tasks", "--no-daemon"], ROOT / "android"),
        "browser": ([shutil.which("npm.cmd") or "npm", "run", "test:live-smoke", "--", "--reporter=junit"], ROOT / "frontend"),
    }
    for identifier in sorted(required):
        if identifier in execution_failures:
            continue
        if (identifier in {"capture_privacy", "mobile_result"} and identifier not in evidence
                and live and emulator and audio_dir):
            stage_output = output / identifier
            script = "verify-capture-feasibility.py" if identifier == "capture_privacy" else "verify-managed-admission.py"
            command = [sys.executable, str(ROOT / "android/scripts" / script), "--report", str(stage_output)]
            if identifier == "mobile_result":
                command.extend(["--mobile-result", "--gate", gate, "--render-dir", str(audio_dir.resolve())])
            print(f"Checking {identifier}…", flush=True)
            executed = _command(identifier + "_execution", command, backend, output, env=provider_env)
            if executed["status"] != "passed":
                checks.append({**executed, "id": identifier})
                continue
            # A successful launcher alone cannot pass an observation gate.
            evidence[identifier] = stage_output / "acceptance.json"
        if identifier in evidence:
            path = evidence[identifier].resolve()
            try:
                checks.append(load_assertion(path, candidate, gate=gate, identifier=identifier))
            except (OSError, ValueError, StopIteration, TypeError, KeyError) as exc:
                checks.append({"id": identifier, "status": "blocked", "evidence": {
                    "reason": f"Current candidate evidence unavailable or invalid: {exc}"}})
        elif identifier in commands:
            command, cwd = commands[identifier]
            print(f"Checking {identifier}…", flush=True)
            checks.append(_command(identifier, command, cwd, output,
                junit=output / f"{identifier}.xml" if identifier in {"backend", "frontend_tests", "browser"} else None,
                junit_glob="app/build/test-results/testDebugUnitTest/TEST-*.xml" if identifier == "android" else None,
                env={**provider_env, **(manifest["backend_environment"] if identifier == "backend" else {}), "PLAYWRIGHT_JUNIT_OUTPUT_FILE": str(output / "browser.xml")},
                timeout=manifest.get("command_timeouts_seconds", {}).get(identifier, 1200)))
        elif identifier == "migration":
            from app.jarvis_tasks.rollout_check import migration_rehearsal
            proof = migration_rehearsal(settings)
            checks.append({"id": identifier, "status": proof["status"], "evidence": proof})
        else:
            checks.append({"id": identifier, "status": "blocked", "evidence": {"reason": "Required live/emulator/provider observation has not been supplied for this candidate"}})
    report = {"schema": SCHEMA, "gate": gate, "candidate": candidate, "checks": checks,
        "finished_at": datetime.now(timezone.utc).isoformat(), "deployment_authorized": False}
    if candidate_identity() != candidate:
        report["checks"].append({"id": "candidate_stability", "status": "failed", "evidence": {"reason": "Source/configuration changed during acceptance"}})
        report["candidate"] = None
    report.update(evaluate(report, candidate, gate=gate))
    (output / "acceptance.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    return report
