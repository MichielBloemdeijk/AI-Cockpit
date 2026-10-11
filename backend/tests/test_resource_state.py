from __future__ import annotations

from app.services.resource_state import directory_snapshot


def test_directory_snapshot_detects_content_change(tmp_path) -> None:
    folder = tmp_path / "shared"
    folder.mkdir()
    file = folder / "note.txt"
    file.write_text("before", encoding="utf-8")
    first = directory_snapshot(str(folder))
    assert first["state"] == "known"
    file.write_text("after", encoding="utf-8")
    assert directory_snapshot(str(folder))["sha256"] != first["sha256"]


def test_directory_snapshot_reports_missing_and_unscannable(tmp_path, monkeypatch) -> None:
    assert directory_snapshot(str(tmp_path / "missing")) == {"state": "missing"}
    folder = tmp_path / "large"
    folder.mkdir()
    (folder / "one.txt").write_text("content", encoding="utf-8")
    monkeypatch.setattr("app.services.resource_state.MAX_BYTES", 1)
    assert directory_snapshot(str(folder)) == {"state": "unknown", "reason": "byte_limit"}
