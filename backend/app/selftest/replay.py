"""Layer L1: replay recorded conversations through the current bookkeeper.

Each recorded case's database is copied, its bookkeeping results are reset, and
the current observer re-assesses the full dialogue with real Responses calls.
The original recording is never modified. Replay assesses the end-state dialogue
in one batch, not the incremental batches a live session would produce.
"""
from __future__ import annotations

import json
from pathlib import Path
import shutil
import sqlite3

from alembic import command
from alembic.config import Config
import yaml

from app.config import settings
from app.selftest import harness as h

LABELS = Path(__file__).with_name("replay_labels.yaml")


def prepare(source: Path, destination: Path) -> Path:
    destination.mkdir(parents=True, exist_ok=True)
    database = destination / "application.db"
    shutil.copy2(source / "application.db", database)
    with sqlite3.connect(database) as db:
        db.execute("DELETE FROM conversation_observations")
        rows = db.execute("SELECT id, required_facts_json FROM task_presentations WHERE handled_by = 'conversation_observer'").fetchall()
        for presentation_id, facts in rows:
            facts = json.loads(facts or "{}")
            facts.pop("communication_assessment", None)
            db.execute("UPDATE task_presentations SET state='pending', handled_at=NULL, handled_by=NULL, "
                "handled_event_id=NULL, required_facts_json=? WHERE id=?", (json.dumps(facts), presentation_id))
        db.execute("UPDATE task_presentations SET required_facts_json=json_remove(required_facts_json, '$.communication_assessment') "
            "WHERE state='pending'")
    return database


async def replay_case(source: Path, destination: Path) -> dict:
    from app.db.session import close_engine, initialize_engine
    from app.services import conversation_observer as module
    database = prepare(source, destination)
    await close_engine()
    settings.database_url = f"sqlite+aiosqlite:///{database.as_posix()}"
    config = Config(settings.alembic_config_path)
    config.set_main_option("sqlalchemy.url", settings.database_url.replace("+aiosqlite", ""))
    command.upgrade(config, "head")
    initialize_engine(settings.database_url)
    settings.jarvis_conversation_observer_mode = "enabled"
    observer = module.ConversationObserver()
    jobs = 0
    try:
        for _ in range(5):
            await observer.collect()
            progressed = False
            while await observer.process_one():
                jobs += 1
                progressed = True
            if not progressed:
                break
    finally:
        await close_engine()
    with sqlite3.connect(database) as db:
        db.row_factory = sqlite3.Row
        presentations = [dict(r) for r in db.execute(
            "SELECT id, task_id, state, handled_by, required_facts_json FROM task_presentations WHERE candidate_type='terminal_result'")]
        observations = [dict(r) for r in db.execute("SELECT status, result_json, usage_json, error FROM conversation_observations")]
    outcomes = []
    for p in presentations:
        assessment = json.loads(p["required_facts_json"] or "{}").get("communication_assessment") or {}
        outcomes.append({"presentation_id": p["id"], "state": p["state"], "classification": assessment.get("classification"),
            "validation_reason": assessment.get("validation_reason")})
    reasons = [o for row in observations for o in (json.loads(row["result_json"] or "{}").get("outcomes") or [])]
    usage = [json.loads(row["usage_json"] or "{}").get("usage") for row in observations]
    return {"jobs": jobs, "outcomes": outcomes, "model_reasons": reasons, "usage": usage,
        "errors": [row["error"] for row in observations if row["error"]]}


async def replay(name: str, *, only: list[str] | None = None) -> dict:
    labels = yaml.safe_load(LABELS.read_text(encoding="utf-8"))["cases"]
    output = h.run_root(name)
    original_url, original_mode = settings.database_url, settings.jarvis_conversation_observer_mode
    results = []
    try:
        for label in labels:
            source = h.ROOT / "data" / label["path"]
            case = source.name
            if only and not any(o in label["path"] for o in only):
                continue
            if not (source / "application.db").exists():
                results.append({"case": case, "expect": label["expect"], "verdict": "missing_recording"})
                continue
            result = await replay_case(source, output / case)
            handled = any(o["state"] == "handled" for o in result["outcomes"])
            relevant = [o for o in result["outcomes"] if o["classification"] or o["state"] == "handled"]
            if label["expect"] == "communicated":
                verdict = "pass" if relevant and all(o["state"] == "handled" for o in relevant) else "fail"
            else:
                verdict = "fail" if handled else "pass"
            results.append({"case": case, "expect": label["expect"], "verdict": verdict, **result})
            print(json.dumps({"case": case, "expect": label["expect"], "verdict": verdict,
                "outcomes": [(o["state"], o["classification"]) for o in result["outcomes"]]}), flush=True)
    finally:
        settings.database_url, settings.jarvis_conversation_observer_mode = original_url, original_mode
    summary = {"passed": sum(r["verdict"] == "pass" for r in results), "total": len(results), "results": results}
    h.write(output / "replay.json", summary)
    return summary
