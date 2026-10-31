"""Check a package before it is installed: the manifest, compatibility and its UI.

The UI check runs the package in headless Chromium under the same sandbox and
CSP as the real host (see `harness.mjs`). It always checks that the app loads
cleanly, then runs the package's declarative UI tests from `tests/ui.json`:

    {"tests": [{"name": "adds a note",
                "records": {"note": [{"key": "a", "value": {"text": "milk"}}]},
                "steps": [{"fill": "#text", "value": "bread"}, {"click": "button[type=submit]"},
                          {"expect_text": "bread"}, {"expect_records": {"collection": "note", "count": 2}}]}]}

Steps are data, never code, so checking a package never runs model-written code
outside the browser sandbox. Records the UI wrote are validated against the
collection schemas afterwards, as the real data API would on write.
"""
from __future__ import annotations

import asyncio
import json
from pathlib import Path
import shutil
from typing import Annotated, Any

from jsonschema import Draft202012Validator
from pydantic import BaseModel, ConfigDict, Field, StrictInt, StrictStr, ValidationError

from app.app_platform.hosting import HOST_CSP, SDK_FILE
from app.app_platform.lifecycle import AppNotFound, get_active, package_digest
from app.app_platform.manifest import ManifestError, compatibility_problems, load_package
from app.config import settings

HARNESS = Path(__file__).parent / "harness.mjs"
TESTS_FILE = Path("tests") / "ui.json"
CHECK_TIMEOUT_SECONDS = 180
Selector = Annotated[str, Field(min_length=1, max_length=300)]


class _Step(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class Fill(_Step):
    fill: Selector
    value: StrictStr = Field(max_length=2000)


class Click(_Step):
    click: Selector


class Press(_Step):
    press: Selector
    key: StrictStr = Field(min_length=1, max_length=40)


class Select(_Step):
    select: Selector
    value: StrictStr = Field(max_length=300)


class Check(_Step):
    check: Selector
    value: bool = True


class Wait(_Step):
    wait: StrictInt = Field(ge=0, le=2000)


class ExpectText(_Step):
    expect_text: StrictStr = Field(min_length=1, max_length=500)
    selector: Selector | None = None


class ExpectCount(_Step):
    expect_count: Selector
    count: StrictInt = Field(ge=0)


class RecordExpectation(_Step):
    collection: StrictStr
    count: StrictInt = Field(ge=0)
    where: dict[str, StrictStr | StrictInt | float | bool | None] = Field(default_factory=dict)


class ExpectRecords(_Step):
    expect_records: RecordExpectation


class SeedRecord(_Step):
    key: StrictStr = Field(min_length=1, max_length=128)
    value: Any


class UiTest(_Step):
    name: StrictStr = Field(min_length=1, max_length=120)
    records: dict[str, list[SeedRecord]] = Field(default_factory=dict)
    steps: list[Fill | Click | Press | Select | Check | Wait | ExpectText | ExpectCount | ExpectRecords] = Field(
        min_length=1, max_length=60)


class UiTests(_Step):
    tests: list[UiTest] = Field(min_length=1, max_length=20)


def _tests(root: Path, collections: dict[str, dict]) -> tuple[list[dict[str, Any]], list[str]]:
    path = root / TESTS_FILE
    if not path.is_file():
        return [], [f"Add UI tests in {TESTS_FILE.as_posix()} that exercise the app's main actions"]
    try:
        tests = UiTests.model_validate(json.loads(path.read_text(encoding="utf-8")))
    except (OSError, json.JSONDecodeError, ValidationError) as exc:
        return [], [f"{TESTS_FILE.as_posix()} is invalid: {exc}"]
    problems = []
    for test in tests.tests:
        for collection, records in test.records.items():
            if collection not in collections:
                problems.append(f"test {test.name!r} seeds unknown collection {collection}")
                continue
            validator = Draft202012Validator(collections[collection])
            problems.extend(f"test {test.name!r} seed {record.key!r} is invalid: {error.message}"
                            for record in records for error in list(validator.iter_errors(record.value))[:1])
    return [test.model_dump(exclude_none=True) for test in tests.tests], problems


async def _run_harness(job: dict[str, Any]) -> dict[str, Any]:
    node = shutil.which("node")
    if node is None:
        raise RuntimeError("Node.js is not available to run the UI check")
    process = await asyncio.create_subprocess_exec(
        node, str(HARNESS), stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE)
    try:
        stdout, stderr = await asyncio.wait_for(process.communicate(json.dumps(job).encode()),
                                                timeout=CHECK_TIMEOUT_SECONDS)
    except TimeoutError:
        process.kill()
        await process.wait()
        raise RuntimeError("The UI check timed out") from None
    if process.returncode != 0:
        raise RuntimeError(f"The UI check could not run: {stderr.decode(errors='replace')[-1500:]}")
    return json.loads(stdout)


async def check_package(root: Path, *, screenshots: Path | None = None) -> dict[str, Any]:
    """Everything that would stop this package from installing or working, as a report."""
    try:
        manifest, schemas = load_package(root)
        digest = package_digest(root)
    except ManifestError as exc:
        return {"ok": False, "problems": [str(exc)], "tests": []}
    problems: list[str] = []
    try:
        active = await get_active(manifest.slug)
    except AppNotFound:
        active = None
    # The exact package already active passes, so a retried install is a no-op.
    if active is not None and not (manifest.version == active.version and package_digest(active.package_path) == digest):
        if manifest.version <= active.version:
            problems.append(f"Set version to {active.version + 1}: version {active.version} is installed")
        old = {name: spec["schema_json"] for name, spec in active.collections.items()}
        problems.extend(f"Incompatible with stored data: {problem}" for problem in compatibility_problems(old, schemas))

    tests, test_problems = _tests(root, schemas)
    problems.extend(test_problems)
    if screenshots is not None:
        shutil.rmtree(screenshots, ignore_errors=True)
        screenshots.mkdir(parents=True)
    job = {
        "playwrightDir": str(settings.app_check_playwright_path), "packageUi": str((root / "ui").resolve()),
        "entry": manifest.ui.entry.removeprefix("ui/"), "sdkFile": str(SDK_FILE), "csp": HOST_CSP,
        "screenshotsDir": str(screenshots) if screenshots else None,
        "slug": manifest.slug, "title": manifest.title, "version": manifest.version, "tests": tests,
    }
    try:
        report = await _run_harness(job)
    except (RuntimeError, OSError, json.JSONDecodeError) as exc:
        return {"ok": False, "problems": [*problems, str(exc)], "tests": []}

    for test in report["tests"]:
        for record in test.pop("records"):
            schema = schemas.get(record["collection"])
            if schema is None:
                test["problems"].append(f"wrote to unknown collection {record['collection']}")
                continue
            for error in list(Draft202012Validator(schema).iter_errors(record["value"]))[:1]:
                test["problems"].append(f"wrote an invalid {record['collection']} record {record['key']!r}: "
                                        f"{error.message}")
        test["ok"] = test["ok"] and not test["problems"]
    failed = [test["name"] for test in report["tests"] if not test["ok"]]
    if failed:
        problems.append(f"UI tests failed: {', '.join(failed)}")
    return {"ok": not problems, "problems": problems, "slug": manifest.slug, "version": manifest.version,
            "tests": report["tests"]}


__all__ = ["check_package"]
