"""Re-transcribe and re-review every run in a run directory with the current reviewer.

Uses the scenario recorded in each run's spec.json, so a later scenario edit
cannot change what an old run is judged against. Old review files are kept
with a suffix.
"""
from __future__ import annotations

import json
from pathlib import Path

from app.selftest import harness as h
from app.selftest.review import review_run


def rereview(output: Path, *, transcribe_again: bool = False) -> list[dict]:
    ledger = h.RequestLedger(output / "request-ledger.json")
    rows = []
    for folder in sorted(p for p in output.iterdir() if (p / "spec.json").exists()):
        scenario = json.loads((folder / "spec.json").read_text(encoding="utf-8"))["case"]
        evidence = json.loads((folder / "evidence.json").read_text(encoding="utf-8"))
        if evidence.get("error") or not evidence.get("recording_complete"):
            rows.append({"folder": folder.name, "skipped": evidence.get("error") or "incomplete recording"})
            continue
        for name in (["assistant-transcription.json"] if transcribe_again else []) + ["review.json", "review-pack.md"]:
            path = folder / name
            if path.exists():
                path.replace(path.with_name(path.stem + ".previous" + path.suffix))
        h.transcribe(folder, ledger)
        review = review_run(folder, scenario, ledger)
        rows.append({"folder": folder.name, "verdict": review["verdict"], "agreed": review.get("agreed"),
            "bookkeeping_correct": review["bookkeeping_correct"], "summary": review["summary"]})
        print(json.dumps(rows[-1], ensure_ascii=False), flush=True)
    h.write(output / "rereview.json", rows)
    return rows


if __name__ == "__main__":
    import sys
    rereview(h.run_root(sys.argv[1]))
