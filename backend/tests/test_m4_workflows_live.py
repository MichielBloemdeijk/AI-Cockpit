"""Paid M4 native integration; routine tests do not invoke providers."""
from __future__ import annotations

from io import BytesIO
import json
import os
from pathlib import Path
from time import monotonic
from uuid import uuid4

from PIL import Image, ImageDraw
import pytest

pytestmark = pytest.mark.provider
from sqlalchemy import select

from app.config import settings
from app.db.session import session_scope
from app.db.tables import ResultPage, ResultPageRevision
from app.services.codex_task_runtime import codex_task_runtime
from app.services.task_service import task_service
from app.services.task_worker import TaskWorker
from app.services.worker_coordinator import worker_coordinator
from test_m4_workflows import admit


@pytest.mark.asyncio
@pytest.mark.skipif(os.environ.get("JARVIS_M4_LIVE") != "1", reason="Paid native/research test is opt-in")
@pytest.mark.parametrize("definition", ["recipe_from_screen@1", "research_screen@1"])
@pytest.mark.parametrize("sample", range(int(os.environ.get("JARVIS_M4_SAMPLES", "1"))))
async def test_m4_actual_native_research_publication(monkeypatch, tmp_path, definition, sample):
    if not settings.openai_api_key:
        pytest.skip("No authorized API credential")
    live_root = settings.backend_root / "data" / f"m4-live-{uuid4()}"
    live_root.mkdir(parents=True)
    monkeypatch.setattr(settings, "codex_profiles_dir", str(live_root / "codex-profiles"))
    monkeypatch.setattr(settings, "task_workspaces_dir", str(live_root / "workspaces"))
    monkeypatch.setattr(settings, "task_research_enabled", True)
    monkeypatch.setattr(settings, "worker_heartbeat_interval_seconds", 1.0)
    image = Image.new("RGB", (640, 320), "white")
    subject = "tomato pasta" if sample % 2 == 0 else "lentil soup"
    ImageDraw.Draw(image).text((40, 80), subject.upper() + "\nVegetarian dish on the menu", fill="black", font_size=28)
    encoded = BytesIO()
    image.save(encoded, format="JPEG")
    args, _ = await admit(monkeypatch, definition, image_bytes=encoded.getvalue(), claim_task=False,
        goal=f"Use the dish named on the screen: give a useful source-backed vegetarian {subject} recipe." if definition.startswith("recipe") else
             "Give a concise sourced explanation of the dish named on this menu, its preparation and background.")
    worker = TaskWorker(runtime=codex_task_runtime)
    assert await worker.activate(process_identity="m4-real-native")
    started = monotonic()
    finished = await worker.run_once()
    elapsed = monotonic() - started
    detail = await task_service.get_task(args.task_id)
    initial_outcome = finished.status
    if finished.status == "waiting_for_input":
        pending = next(question for question in detail.questions if question.state == "asked")
        # Fixture-authoritative answer, never human input or production auto-answering.
        await task_service.answer_question(args.task_id, pending.id,
            answer=f"The menu says {subject}; it is vegetarian. Give the requested sourced result for that dish.",
            idempotency_key="fixture-clarification")
        started = monotonic()
        finished = await worker.run_once()
        elapsed += monotonic() - started
        detail = await task_service.get_task(args.task_id)
    async with session_scope() as session:
        page = (await session.execute(select(ResultPage).where(ResultPage.task_id == args.task_id))).scalar_one()
        revision = await session.get(ResultPageRevision, page.current_revision_id) if page.current_revision_id else None
        content = revision.content_json if revision else None
        first_public_id = page.public_id
    report = {"definition": definition, "sample": sample, "subject": subject,
              "definition_digest": detail.runs[0].configuration_json["workflow"]["digest"],
              "model": detail.runs[0].configuration_json["model"],
              "reasoning_effort": detail.runs[0].configuration_json["reasoning_effort"],
              "codex_runtime": "0.155.1", "search_model": settings.codex_task_model,
              "status": "passed" if finished.status == "completed" and content else "failed", "outcome": finished.status,
              "initial_outcome": initial_outcome, "fixture_answered": initial_outcome == "waiting_for_input",
              "terminal_summary": detail.task.terminal_summary, "native": True, "real_research": True,
              "task_id": args.task_id, "result_kind": page.result_kind, "result_id": page.public_id,
              "cold_execution_seconds": round(elapsed, 3), "content": content,
              "native_usage": [attempt.usage_summary_json for attempt in detail.attempts],
              "native_research": [{"sequence": event.sequence, "payload": event.payload_json}
                  for event in detail.events if event.event_type == "agent.native.item"
                  and event.payload_json.get("item", {}).get("type") == "webSearch"],
              "invoice_cost": None}

    if content:
        await task_service.continue_task(args.task_id,
            content="Revise the existing result to make the wording of the preparation instructions clearer and shorter. Keep the existing facts and explicit constraints. Reuse retained sources; no fresh research is needed. Publish the revised result.",
            idempotency_key="live-followup", origin="test")
        started = monotonic()
        followup = await worker.run_once()
        report["ready_followup_seconds"] = round(monotonic() - started, 3)
        report["followup_outcome"] = followup.status
        detail = await task_service.get_task(args.task_id)
        report["followup_terminal_summary"] = detail.task.terminal_summary
        async with session_scope() as session:
            page = (await session.execute(select(ResultPage).where(ResultPage.task_id == args.task_id))).scalar_one()
            revision = await session.get(ResultPageRevision, page.current_revision_id)
            report["followup_content"] = revision.content_json
            report["same_url_revision"] = page.public_id == first_public_id and page.revision_number == 2
        report["native_usage"] = [attempt.usage_summary_json for attempt in detail.attempts]
        searches = [event for event in detail.events if event.event_type == "agent.native.item" and event.payload_json.get("item", {}).get("type") == "webSearch"]
        report["reused_without_new_search"] = len(searches) == len(report["native_research"])
        if followup.status != "completed" or not report["same_url_revision"] or not report["reused_without_new_search"]:
            report["status"] = "failed"
    if os.environ.get("JARVIS_M4_REPORT_DIR"):
        evidence_path = live_root / "private-evidence.json"
        evidence_path.write_text(json.dumps({"native_research": report["native_research"],
            "provenance": "provider-backed; no independent local download"}, ensure_ascii=True, indent=2), encoding="utf-8")
        report["private_evidence_path"] = str(evidence_path)
        folder = Path(os.environ["JARVIS_M4_REPORT_DIR"])
        folder.mkdir(parents=True, exist_ok=True)
        (folder / f"{definition.split('@')[0]}-{sample}.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    assert report["status"] == "passed", report
    assert all(usage for usage in report["native_usage"]), "Native usage must be observed, not inferred"
    assert report["native_research"], "Native research must be observed"
