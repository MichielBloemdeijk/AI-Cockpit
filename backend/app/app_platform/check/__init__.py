"""Check a package before it is installed: the manifest, compatibility, its UI and functions.

The UI check runs the package in headless Chromium under the same sandbox and
CSP as the real host (see `harness.mjs`). It always checks that the app loads
cleanly, then runs the package's declarative UI tests from `tests/ui.json`:

    {"tests": [{"name": "adds a note",
                "records": {"note": [{"key": "a", "value": {"text": "milk"}}]},
                "steps": [{"fill": "#text", "value": "bread"}, {"click": "button[type=submit]"},
                          {"expect_text": "bread"}, {"expect_records": {"collection": "note", "count": 2}}]}]}

A test may set `"open": {"collection": "note", "key": "a"}` to start the app on
that record, as Cockpit does when a link or Jarvis opens it. Confirmation
dialogs (`confirm()`) are accepted, as a user would. `"files"` seeds the file
store (`{"a.txt": {"text": "hi"}}`, or `"base64"`), `"functions"` gives the
results the UI's function calls return (`{"import_url": {"output": {...}}}` or
`{"error": "..."}`), and the `upload` and `expect_files` steps cover files. A UI
that uploads files needs a test with an upload step, and one that shows stored
files needs a test that seeds some.

UI tests run with the date fixed at Monday 2026-01-05 09:00 Europe/Amsterdam,
so date-dependent UIs render the same on every run. UI steps are data, never code. Records the UI wrote are validated against the
collection schemas afterwards, as the real data API would on write.

Every function needs at least one test in `tests/functions.json`:

    {"tests": [{"name": "imports a page", "function": "import_url", "input": {"url": "https://example.org/a"},
                "fetch": [{"url": "https://example.org/a", "body": "<h1>Soup</h1>"}],
                "expect": {"output": {"title": "Soup"}, "records": [{"collection": "recipe", "count": 1}]}}]}

They run the function's code in the real function host, with the same limits
and guard as in use, against in-memory records and files the test seeds.
Fetches must be mocked, and the URL must still be in the app's network grants.
`expect.output` matches when every expected key or item is equal (extra keys in
the output are fine); `expect.error` is text the error must contain.
"""
from __future__ import annotations

import asyncio
from base64 import b64decode, b64encode
import binascii
import json
from pathlib import Path
import shutil
from typing import Annotated, Any

from jsonschema import Draft202012Validator
from pydantic import BaseModel, ConfigDict, Field, StrictInt, StrictStr, ValidationError

from app.app_platform.files import FileError, check_name
from app.app_platform.function_host import FetchMock, MemoryServices, execute
from app.app_platform.hosting import HOST_CSP, SDK_FILE
from app.app_platform import system
from app.app_platform.lifecycle import AppNotFound, get_active, grant_problems, package_digest, schema_change_problems
from app.app_platform.manifest import AppManifest, HttpTrigger, ManifestError, load_package
from app.app_platform.workflows import workflow_conflicts
from app.config import settings

HARNESS = Path(__file__).parent / "harness.mjs"
TESTS_FILE = Path("tests") / "ui.json"
FUNCTION_TESTS_FILE = Path("tests") / "functions.json"
CHECK_TIMEOUT_SECONDS = 180
FUNCTION_TEST_TIMEOUT_SECONDS = 60
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


class Upload(_Step):
    """Choose a file in an <input type=file>, as a user would; contents as text or base64."""
    upload: Selector
    name: StrictStr = Field(min_length=1, max_length=200)
    text: StrictStr | None = None
    base64: StrictStr | None = None
    content_type: StrictStr = "text/plain"


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


class FileExpectation(_Step):
    count: StrictInt = Field(ge=0)
    prefix: StrictStr = ""


class ExpectFiles(_Step):
    expect_files: FileExpectation


class SeedRecord(_Step):
    key: StrictStr = Field(min_length=1, max_length=128)
    value: Any


class SeedFile(_Step):
    text: StrictStr | None = None
    base64: StrictStr | None = None
    content_type: StrictStr = "text/plain"

    def data(self) -> bytes:
        if self.base64 is not None:
            return b64decode(self.base64, validate=True)
        return (self.text or "").encode()


class FunctionResult(_Step):
    """What the UI's call of a function returns in a UI test: its output, or an error."""
    output: Any = None
    error: StrictStr | None = None


class OpenTarget(_Step):
    collection: StrictStr = Field(min_length=1, max_length=48)
    key: StrictStr = Field(min_length=1, max_length=128)


class UiTest(_Step):
    name: StrictStr = Field(min_length=1, max_length=120)
    records: dict[str, list[SeedRecord]] = Field(default_factory=dict)
    files: dict[str, SeedFile] = Field(default_factory=dict)
    functions: dict[str, FunctionResult] = Field(default_factory=dict)
    open: OpenTarget | None = None
    steps: list[Fill | Click | Press | Select | Check | Wait | Upload | ExpectText | ExpectCount | ExpectRecords
                | ExpectFiles] = Field(min_length=1, max_length=60)


class UiTests(_Step):
    tests: list[UiTest] = Field(min_length=1, max_length=20)


class FetchMockSpec(_Step):
    url: StrictStr = Field(min_length=1, max_length=2000)
    method: StrictStr = "GET"
    status: StrictInt = Field(default=200, ge=100, le=599)
    headers: dict[str, StrictStr] = Field(default_factory=dict)
    body: StrictStr = ""
    json_body: Any = Field(default=None, alias="json")


class FunctionExpectation(_Step):
    output: Any = None
    error: StrictStr | None = None
    records: list[RecordExpectation] = Field(default_factory=list)
    files: FileExpectation | None = None


class FunctionTest(_Step):
    name: StrictStr = Field(min_length=1, max_length=120)
    function: StrictStr
    input: Any = None
    records: dict[str, list[SeedRecord]] = Field(default_factory=dict)
    files: dict[str, SeedFile] = Field(default_factory=dict)
    fetch: list[FetchMockSpec] = Field(default_factory=list, max_length=20)
    expect: FunctionExpectation = Field(default_factory=FunctionExpectation)


class FunctionTests(_Step):
    tests: list[FunctionTest] = Field(min_length=1, max_length=40)


def _http_functions(manifest: AppManifest) -> set[str]:
    return {name for name, spec in manifest.functions.items() if isinstance(spec.trigger, HttpTrigger)}


def _read_only(manifest: AppManifest) -> set[str]:
    return set(manifest.grants.read) - set(manifest.grants.write)


async def _granted_schemas(manifest: AppManifest) -> dict[str, dict]:
    """{"recipes.recipe": schema} for every granted collection that exists."""
    granted = {}
    for grant in manifest.grants.readable():
        owner, _, name = grant.partition(".")
        if owner == "system":
            granted[grant] = system.SCHEMAS[name]
            continue
        try:
            provider = await get_active(owner)
        except AppNotFound:
            continue
        if name in provider.collections:
            granted[grant] = provider.collections[name]["schema_json"]
    return granted


def _seed_problems(test: UiTest | FunctionTest, manifest: AppManifest, collections: dict[str, dict]) -> list[str]:
    problems = []
    for collection, records in test.records.items():
        if collection not in collections:
            problems.append(f"test {test.name!r} seeds unknown collection {collection}")
            continue
        validator = Draft202012Validator(collections[collection])
        problems.extend(f"test {test.name!r} seed {record.key!r} is invalid: {error.message}"
                        for record in records for error in list(validator.iter_errors(record.value))[:1])
    if test.files and manifest.files is None:
        problems.append(f"test {test.name!r} seeds files, but the app declares no files section")
    for name, seed in test.files.items():
        try:
            check_name(name)
            seed.data()
        except (FileError, binascii.Error) as exc:
            problems.append(f"test {test.name!r} seeds file {name!r}: {exc}")
    return problems


def _tests(root: Path, manifest: AppManifest, collections: dict[str, dict]) -> tuple[list[dict[str, Any]], list[str]]:
    path = root / TESTS_FILE
    if not path.is_file():
        return [], [f"Add UI tests in {TESTS_FILE.as_posix()} that exercise the app's main actions"]
    try:
        tests = UiTests.model_validate(json.loads(path.read_text(encoding="utf-8")))
    except (OSError, json.JSONDecodeError, ValidationError) as exc:
        return [], [f"{TESTS_FILE.as_posix()} is invalid: {exc}"]
    problems = []
    callable_functions = _http_functions(manifest)
    for test in tests.tests:
        if test.open is not None and test.open.collection not in collections:
            problems.append(f"test {test.name!r} opens unknown collection {test.open.collection}")
        problems.extend(_seed_problems(test, manifest, collections))
        problems.extend(f"test {test.name!r} gives a result for {name}, which is not a function with an http trigger"
                        for name in test.functions if name not in callable_functions)
    problems.extend(_file_coverage_problems(root, tests))
    if problems:
        return [], problems
    serialized = []
    for test in tests.tests:
        value = test.model_dump(exclude_none=True)
        # The harness gets every file as base64, whichever way the test wrote it.
        value["files"] = {name: {"base64": b64encode(seed.data()).decode(), "content_type": seed.content_type}
                          for name, seed in test.files.items()}
        serialized.append(value)
    return serialized, []


def _ui_source(root: Path) -> str:
    return "\n".join(path.read_text(encoding="utf-8", errors="replace") for path in sorted((root / "ui").rglob("*"))
                     if path.suffix in {".html", ".js", ".mjs"} and path.is_file())


def _file_coverage_problems(root: Path, tests: UiTests) -> list[str]:
    """A UI that uploads or shows files needs tests that do the same."""
    source = _ui_source(root)
    problems = []
    if "files.put" in source and not any(isinstance(step, Upload) for test in tests.tests for step in test.steps):
        problems.append(f"The UI uploads files: add a test in {TESTS_FILE.as_posix()} with an upload step "
                        "and expect_files")
    if "files.get" in source and not any(test.files for test in tests.tests):
        problems.append(f"The UI shows stored files: add a test in {TESTS_FILE.as_posix()} that seeds \"files\" "
                        "and checks they are shown")
    return problems


def _matches(expected: Any, actual: Any) -> bool:
    if isinstance(expected, dict):
        return isinstance(actual, dict) and all(key in actual and _matches(value, actual[key])
                                                for key, value in expected.items())
    if isinstance(expected, list):
        return isinstance(actual, list) and len(expected) == len(actual) and all(
            _matches(value, item) for value, item in zip(expected, actual))
    return expected == actual


async def _run_function_test(root: Path, manifest: AppManifest, collections: dict[str, dict],
                             test: FunctionTest) -> dict[str, Any]:
    report: dict[str, Any] = {"name": f"function {test.function}: {test.name}", "ok": False, "error": None,
                              "problems": _seed_problems(test, manifest, collections)}
    spec = manifest.functions.get(test.function)
    if spec is None:
        report["error"] = f"the app has no function {test.function}"
        return report
    if report["problems"]:
        return report
    services = MemoryServices(
        grants=manifest.grants.network, schemas=collections, read_only=_read_only(manifest),
        has_files=manifest.files is not None,
        records={collection: {record.key: record.value for record in records}
                 for collection, records in test.records.items()},
        files={name: (seed.data(), seed.content_type) for name, seed in test.files.items()},
        mocks=[FetchMock(url=mock.url, method=mock.method, status=mock.status, headers=mock.headers,
                         body=json.dumps(mock.json_body).encode() if mock.json_body is not None else mock.body.encode())
               for mock in test.fetch])
    result = await execute(root / spec.file, input=test.input, trigger="test",
                           app={"slug": manifest.slug, "title": manifest.title, "version": manifest.version},
                           timeout=min(spec.timeout_seconds, FUNCTION_TEST_TIMEOUT_SECONDS), services=services)
    report["output"], report["logs"] = result.output, result.logs[-1000:]
    expect = test.expect
    if expect.error is not None:
        if result.ok:
            report["problems"].append(f"expected an error containing {expect.error!r}, but it succeeded")
        elif expect.error not in result.error:
            report["problems"].append(f"expected an error containing {expect.error!r}, got {result.error!r}")
    elif not result.ok:
        report["error"] = "\n".join(part for part in (result.error, result.traceback[-1500:]) if part)
    elif "output" in expect.model_fields_set and not _matches(expect.output, result.output):
        report["problems"].append(f"expected output {json.dumps(expect.output)[:500]}, "
                                  f"got {json.dumps(result.output)[:500]}")
    for expectation in expect.records:
        found = [record for (collection, _), record in services.records.items()
                 if collection == expectation.collection
                 and all(record["value"].get(field) == value for field, value in expectation.where.items())]
        if len(found) != expectation.count:
            report["problems"].append(f"expected {expectation.count} {expectation.collection} records "
                                      f"matching {expectation.where}, found {len(found)}")
    if expect.files is not None:
        count = sum(1 for name in services.files if name.startswith(expect.files.prefix))
        if count != expect.files.count:
            report["problems"].append(f"expected {expect.files.count} files starting with "
                                      f"{expect.files.prefix!r}, found {count}")
    report["ok"] = report["error"] is None and not report["problems"]
    return report


async def _function_tests(root: Path, manifest: AppManifest, collections: dict[str, dict]) -> tuple[
        list[dict[str, Any]], list[str]]:
    """Run tests/functions.json in the function host; returns the test reports and problems."""
    if not manifest.functions:
        return [], []
    path = root / FUNCTION_TESTS_FILE
    if not path.is_file():
        return [], [f"Add function tests in {FUNCTION_TESTS_FILE.as_posix()}: at least one per function"]
    try:
        tests = FunctionTests.model_validate(json.loads(path.read_text(encoding="utf-8")))
    except (OSError, json.JSONDecodeError, ValidationError) as exc:
        return [], [f"{FUNCTION_TESTS_FILE.as_posix()} is invalid: {exc}"]
    tested = {test.function for test in tests.tests}
    problems = [f"Add a test for function {name} in {FUNCTION_TESTS_FILE.as_posix()}"
                for name in manifest.functions if name not in tested]
    reports = [await _run_function_test(root, manifest, collections, test) for test in tests.tests]
    return reports, problems


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
    conflicts = workflow_conflicts(manifest.slug, manifest.workflows)
    if conflicts:
        problems.append(f"Rename these workflows; their ids are already in use: {', '.join(conflicts)}")
    try:
        active = await get_active(manifest.slug)
    except AppNotFound:
        active = None
    # The exact package already active passes, so a retried install is a no-op.
    if active is not None and not (manifest.version == active.version and package_digest(active.package_path) == digest):
        if manifest.version <= active.version:
            problems.append(f"Set version to {active.version + 1}: version {active.version} is installed")
        old = {name: spec["schema_json"] for name, spec in active.collections.items()}
        problems.extend(f"Incompatible with stored data: {problem}"
                        for problem in await schema_change_problems(manifest.slug, old, schemas))
    problems.extend(await grant_problems(manifest.slug, manifest.grants.model_dump()))
    # Tests may seed and use granted collections under their qualified names.
    collections = {**schemas, **await _granted_schemas(manifest)}

    tests, test_problems = _tests(root, manifest, collections)
    problems.extend(test_problems)
    function_reports, function_problems = await _function_tests(root, manifest, collections)
    problems.extend(function_problems)
    if screenshots is not None:
        shutil.rmtree(screenshots, ignore_errors=True)
        screenshots.mkdir(parents=True)
    job = {
        "playwrightDir": str(settings.app_check_playwright_path), "packageUi": str((root / "ui").resolve()),
        "entry": manifest.ui.entry.removeprefix("ui/"), "sdkFile": str(SDK_FILE), "csp": HOST_CSP,
        "screenshotsDir": str(screenshots) if screenshots else None,
        "slug": manifest.slug, "title": manifest.title, "version": manifest.version, "tests": tests,
        "httpFunctions": sorted(_http_functions(manifest)),
        "readOnly": sorted(_read_only(manifest)),
    }
    try:
        report = await _run_harness(job)
    except (RuntimeError, OSError, json.JSONDecodeError) as exc:
        return {"ok": False, "problems": [*problems, str(exc)], "tests": function_reports}

    for test in report["tests"]:
        for record in test.pop("records"):
            schema = collections.get(record["collection"])
            if schema is None:
                test["problems"].append(f"wrote to unknown collection {record['collection']}")
                continue
            for error in list(Draft202012Validator(schema).iter_errors(record["value"]))[:1]:
                test["problems"].append(f"wrote an invalid {record['collection']} record {record['key']!r}: "
                                        f"{error.message}")
        test["ok"] = test["ok"] and not test["problems"]
    report["tests"].extend(function_reports)
    failed = [test["name"] for test in report["tests"] if not test["ok"]]
    if failed:
        problems.append(f"Tests failed: {', '.join(failed)}")
    return {"ok": not problems, "problems": problems, "slug": manifest.slug, "version": manifest.version,
            "tests": report["tests"]}


__all__ = ["check_package"]
