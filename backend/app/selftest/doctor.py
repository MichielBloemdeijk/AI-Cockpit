"""Detect, diagnose, propose: run diagnostics on a database and on a recorded run.

Run-level findings come from the harness evidence and the agent review; database
findings come from app.services.diagnostics. Output is a diagnosis.json that a
coding agent can act on. Nothing is repaired automatically.
"""
from __future__ import annotations

import json
from pathlib import Path

from app.config import settings
from app.selftest import harness as h


async def diagnose_database(database: Path, *, live: bool = True) -> list[dict]:
    from app.db.session import close_engine, initialize_engine, session_scope
    from app.services.diagnostics import diagnose
    original = settings.database_url
    await close_engine()
    settings.database_url = f"sqlite+aiosqlite:///{database.as_posix()}"
    initialize_engine(settings.database_url)
    try:
        async with session_scope() as db:
            return await diagnose(db, live=live)
    finally:
        await close_engine()
        settings.database_url = original


def run_findings(folder: Path) -> list[dict]:
    findings = []
    evidence = json.loads((folder / "evidence.json").read_text(encoding="utf-8")) if (folder / "evidence.json").exists() else {}
    if evidence.get("paid_failure"):
        findings.append({"severity": "high", "component": "provider", "symptom": evidence["paid_failure"],
            "evidence": {"folder": folder.name}, "proposed_fix": "Check the API key, billing and quota; no paid retry."})
    if evidence.get("error") or evidence.get("capture_error"):
        findings.append({"severity": "high", "component": "selftest_harness",
            "symptom": evidence.get("error") or evidence.get("capture_error"),
            "evidence": {"driver_log": str(folder / "driver.log"), "backend_log": str(folder / "backend.log")},
            "proposed_fix": "Read driver.log and backend.log; a scenario step timing out usually means Jarvis never "
                "admitted the task or the session did not connect."})
    review = json.loads((folder / "review.json").read_text(encoding="utf-8")) if (folder / "review.json").exists() else None
    if review and review["verdict"] == "material":
        findings.append({"severity": "high", "component": "conversation_quality", "symptom": review["summary"],
            "evidence": {"issues": review["issues"], "independent_agrees": review.get("agreed"),
                "pack": str(folder / "review-pack.md")},
            "proposed_fix": "Reproduce with this scenario; decide whether the cause is the Live update policy "
                "(live_task_updates.py), the notes (assistant_notes.py) or bookkeeping (conversation_observer.py). "
                "If bookkeeping, add the recording to replay_labels.yaml before changing it."})
    if review and not review.get("bookkeeping_correct", True):
        findings.append({"severity": "high", "component": "conversation_observer",
            "symptom": "The communication record does not match what reached the user",
            "evidence": {"pack": str(folder / "review-pack.md")},
            "proposed_fix": "Add this recording to app/selftest/replay_labels.yaml with the agreed truth and iterate "
                "on conversation_observer.py with the L1 replay."})
    return findings


async def diagnose_run(folder: Path) -> list[dict]:
    findings = run_findings(folder)
    if (folder / "application.db").exists():
        findings += await diagnose_database(folder / "application.db", live=False)
    h.write(folder / "diagnosis.json", findings)
    return findings
