"""The shared function host: runs app functions in bounded child processes.

Each run is a fresh `function_runner.py` process with a timeout, a memory
limit (a Windows job object), no inherited secrets and the soft guard described
there. Its platform calls (data, files, fetch) are served here, scoped to the
calling app and its grants. Every real run is logged in `app_function_runs`;
a failure is also reported as an app error, which is what repairs start from.

The package check runs function tests through the same host with in-memory
services (`MemoryServices`), so a test exercises the same process and limits.
"""
from __future__ import annotations

import asyncio
import base64
from dataclasses import dataclass, field
import json
import os
from pathlib import Path
import sys
from time import monotonic
from typing import Any
import weakref

from jsonschema import Draft202012Validator
from sqlalchemy import delete, select

from app.app_platform import files as app_files
from app.app_platform import network
from app.app_platform.events import event_depth
from app.app_platform.lifecycle import ActiveApp, get_active
from app.app_platform.records import RecordError, delete_record, get_record, put_record, query_records
from app.app_platform.store import AppFunctionRun, apps_session, utc_now

RUNNER = Path(__file__).parent / "function_runner.py"
MEMORY_LIMIT_BYTES = 512 * 1024 * 1024
OUTPUT_LIMIT_BYTES = 256 * 1024
LINE_LIMIT_BYTES = 64 * 1024 * 1024
RUNS_KEPT_PER_APP = 100
CONCURRENT_RUNS = 4
_slots: "weakref.WeakKeyDictionary[asyncio.AbstractEventLoop, asyncio.Semaphore]" = weakref.WeakKeyDictionary()
# Variables a Python child needs on Windows; nothing else (no API keys) is passed on.
CHILD_ENVIRONMENT = ("SYSTEMROOT", "WINDIR", "TEMP", "TMP", "PATHEXT", "COMSPEC", "NUMBER_OF_PROCESSORS")


class FunctionError(ValueError):
    """The function does not exist or cannot be called this way."""


@dataclass(slots=True)
class RunResult:
    status: str  # ok, error or timeout
    output: Any = None
    error: str = ""
    traceback: str = ""
    logs: str = ""
    duration_ms: int = 0

    @property
    def ok(self) -> bool:
        return self.status == "ok"

    def view(self) -> dict[str, Any]:
        view = {"ok": self.ok, "status": self.status, "duration_ms": self.duration_ms}
        if self.ok:
            view["output"] = self.output
        else:
            view["error"] = self.error
        if self.logs:
            view["logs"] = self.logs
        return view


def _b64(value: str | None) -> bytes | None:
    return base64.b64decode(value) if value else None


class Services:
    """Platform calls a running function may make; subclasses serve them."""

    def __init__(self, grants: list[str]) -> None:
        self.grants = grants

    async def call(self, method: str, params: dict[str, Any]) -> Any:
        if method == "fetch":
            return await self.fetch(params)
        handler = {
            "data.get": self.data_get, "data.put": self.data_put, "data.delete": self.data_delete,
            "data.query": self.data_query, "files.list": self.files_list, "files.read": self.files_read,
            "files.write": self.files_write, "files.delete": self.files_delete,
        }.get(method)
        if handler is None:
            raise FunctionError(f"Unknown platform call {method}")
        return await handler(**params)

    async def fetch(self, params: dict[str, Any]) -> dict[str, Any]:
        return await network.fetch(self.grants, url=str(params.get("url") or ""), method=params.get("method") or "GET",
                                   headers=params.get("headers") or {}, body=_b64(params.get("body_base64")),
                                   timeout=params.get("timeout") or 20)


class AppServices(Services):
    """The real data, file store and network of one installed app."""

    def __init__(self, app: ActiveApp) -> None:
        super().__init__(list((app.manifest.get("grants") or {}).get("network") or []))
        self.slug = app.slug

    async def data_get(self, collection, key):
        return await get_record(self.slug, collection, key)

    async def data_put(self, collection, key, value, expected_revision=None):
        return await put_record(self.slug, collection, key, value, expected_revision=expected_revision)

    async def data_delete(self, collection, key, expected_revision=None):
        return await delete_record(self.slug, collection, key, expected_revision=expected_revision)

    async def data_query(self, collection, where=None, order=None, limit=50):
        return await query_records(self.slug, collection, where=where, order=order, limit=limit or 50)

    async def files_list(self, prefix=None):
        return await app_files.list_files(self.slug, prefix)

    async def files_read(self, name):
        found = await app_files.read_file(self.slug, name)
        return None if found is None else {**found[0], "data_base64": base64.b64encode(found[1]).decode()}

    async def files_write(self, name, data_base64, content_type=None):
        return await app_files.put_file(self.slug, name, base64.b64decode(data_base64), content_type)

    async def files_delete(self, name):
        return await app_files.delete_file(self.slug, name)


@dataclass(slots=True)
class FetchMock:
    url: str
    method: str = "GET"
    status: int = 200
    headers: dict[str, str] = field(default_factory=dict)
    body: bytes = b""


class MemoryServices(Services):
    """Records and files in memory and mocked fetches, for function tests in the package check."""

    def __init__(self, *, grants: list[str], schemas: dict[str, dict], has_files: bool,
                 records: dict[str, dict[str, Any]], files: dict[str, tuple[bytes, str]],
                 mocks: list[FetchMock]) -> None:
        super().__init__(grants)
        self.schemas, self.has_files, self.mocks = schemas, has_files, mocks
        self.records = {(collection, key): {"key": key, "value": value, "revision": 1}
                        for collection, values in records.items() for key, value in values.items()}
        self.files = dict(files)

    def _collection(self, collection):
        if collection not in self.schemas:
            raise RecordError(f"The app has no collection {collection}")

    def _store(self):
        if not self.has_files:
            raise app_files.FileError("The app has no file store; declare \"files\" in its manifest")

    async def data_get(self, collection, key):
        self._collection(collection)
        return self.records.get((collection, key))

    async def data_put(self, collection, key, value, expected_revision=None):
        self._collection(collection)
        errors = list(Draft202012Validator(self.schemas[collection]).iter_errors(value))
        if errors:
            raise RecordError(f"{collection} record is invalid: {errors[0].message}")
        current = self.records.get((collection, key))
        revision = current["revision"] if current else 0
        if expected_revision is not None and expected_revision != revision:
            raise RecordError(f"Expected revision {expected_revision}, found {revision}")
        record = {"key": key, "value": value, "revision": revision + 1}
        self.records[(collection, key)] = record
        return record

    async def data_delete(self, collection, key, expected_revision=None):
        self._collection(collection)
        return self.records.pop((collection, key), None) is not None

    async def data_query(self, collection, where=None, order=None, limit=50):
        self._collection(collection)
        found = [record for (name, _), record in self.records.items() if name == collection
                 and all(record["value"].get(field) == value for field, value in (where or {}).items())]
        if order:
            field = order.removeprefix("-")
            found.sort(key=lambda record: str(record["key"] if field == "key" else record["value"].get(field, "")),
                       reverse=order.startswith("-"))
        return found[:limit or 50]

    async def files_list(self, prefix=None):
        self._store()
        return [{"name": name, "size": len(data), "content_type": content_type}
                for name, (data, content_type) in sorted(self.files.items()) if name.startswith(prefix or "")]

    async def files_read(self, name):
        self._store()
        found = self.files.get(name)
        return None if found is None else {"name": name, "size": len(found[0]), "content_type": found[1],
                                           "data_base64": base64.b64encode(found[0]).decode()}

    async def files_write(self, name, data_base64, content_type=None):
        self._store()
        app_files.check_name(name)
        data = base64.b64decode(data_base64)
        self.files[name] = (data, content_type or app_files.DEFAULT_TYPE)
        return {"name": name, "size": len(data), "content_type": self.files[name][1]}

    async def files_delete(self, name):
        self._store()
        return self.files.pop(name, None) is not None

    async def fetch(self, params):
        url, method = str(params.get("url") or ""), str(params.get("method") or "GET").upper()
        network.check_url(url, self.grants)  # a test fails when the real call would be refused
        for mock in self.mocks:
            if mock.url == url and mock.method.upper() == method:
                return {"status": mock.status, "url": url, "headers": mock.headers,
                        "body_base64": base64.b64encode(mock.body).decode()}
        raise network.NetworkError(f"The test has no fetch mock for {method} {url}")


def _run_slots() -> asyncio.Semaphore:
    """At most CONCURRENT_RUNS functions run at once across all apps; more wait their turn."""
    loop = asyncio.get_running_loop()
    if loop not in _slots:
        _slots[loop] = asyncio.Semaphore(CONCURRENT_RUNS)
    return _slots[loop]


def _child_environment() -> dict[str, str]:
    environment = {name: os.environ[name] for name in CHILD_ENVIRONMENT if name in os.environ}
    return {**environment, "PYTHONIOENCODING": "utf-8", "PYTHONUTF8": "1"}


async def execute(file: Path, *, input: Any, app: dict[str, Any], trigger: str, timeout: float,
                  services: Services) -> RunResult:
    """Run `handle(input, cockpit)` from `file` in a fresh child process."""
    from app.services.windows_job import close_job, contain_child_process

    started = monotonic()
    folder = file.parent.resolve()
    async with _run_slots():
        process = await asyncio.create_subprocess_exec(
            sys.executable, "-I", "-B", str(RUNNER), cwd=str(folder), env=_child_environment(),
            stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
            limit=LINE_LIMIT_BYTES)
        job = None
        try:
            try:
                job = contain_child_process(process.pid, memory_bytes=MEMORY_LIMIT_BYTES)
            except OSError:
                job = None  # the limit is a safeguard, not a condition for running
            result = await asyncio.wait_for(_converse(process, file, folder, input, app, trigger, services), timeout)
        except TimeoutError:
            result = RunResult("timeout", error=f"The function took longer than {timeout:g} seconds")
        finally:
            if process.returncode is None:
                process.kill()
            close_job(job)
            stderr = (await process.stderr.read())[-2000:].decode(errors="replace") if process.stderr else ""
            await process.wait()
        if not result.ok and not result.error:
            result.error = f"The function process ended unexpectedly (exit code {process.returncode})"
            if process.returncode in {3221225495, 0xC0000017}:  # out of memory
                result.error = "The function ran out of memory (512 MiB)"
        if stderr.strip() and not result.ok:
            result.traceback = (result.traceback + "\n" + stderr).strip()[-4000:]
    result.duration_ms = int((monotonic() - started) * 1000)
    return result


async def _converse(process, file: Path, folder: Path, input: Any, app: dict[str, Any], trigger: str,
                    services: Services) -> RunResult:
    def send(message: dict[str, Any]) -> bytes:
        return json.dumps(message, separators=(",", ":")).encode() + b"\n"

    assert process.stdin and process.stdout
    process.stdin.write(send({"file": str(file.resolve()), "input": input, "app": app, "trigger": trigger,
                              "read_roots": [str(folder)]}))
    await process.stdin.drain()
    while True:
        try:
            line = await process.stdout.readline()
        except ValueError:
            return RunResult("error", error="The function sent a message larger than 64 MiB")
        if not line:
            return RunResult("error")
        message = json.loads(line)
        if message.get("done"):
            if not message.get("ok"):
                return RunResult("error", error=message.get("error") or "The function failed",
                                 traceback=message.get("traceback") or "", logs=message.get("logs") or "")
            if len(json.dumps(message.get("output")).encode()) > OUTPUT_LIMIT_BYTES:
                return RunResult("error", error="The function's output is larger than 256 KiB",
                                 logs=message.get("logs") or "")
            return RunResult("ok", output=message.get("output"), logs=message.get("logs") or "")
        try:
            reply = {"id": message["id"], "ok": True,
                     "result": await services.call(str(message["call"]), dict(message.get("params") or {}))}
        except (ValueError, TypeError, KeyError, LookupError, OSError) as exc:
            reply = {"id": message.get("id"), "ok": False, "error": str(exc) or type(exc).__name__}
        process.stdin.write(send(reply))
        await process.stdin.drain()


def _clip(value: Any, limit: int = 4000) -> str:
    text = value if isinstance(value, str) else json.dumps(value, default=str)
    return text if len(text) <= limit else text[: limit - 1] + "…"


async def run_function(slug: str, name: str, input: Any = None, *, trigger: str = "call",
                       depth: int = 0) -> RunResult:
    """Run an installed app's function for real and log it.

    trigger is "call" (Jarvis or the UI), "schedule" or "event". `depth` is the
    event chain depth of what triggered it; the run's own writes are one deeper.
    """
    from app.app_platform.errors import record_error

    app = await get_active(slug)
    spec = (app.manifest.get("functions") or {}).get(name)
    if spec is None:
        raise FunctionError(f"{app.title} has no function {name}")
    started_at = utc_now()
    token = event_depth.set(depth + 1)
    try:
        result = await execute(app.package_path / spec["file"], input=input, trigger=trigger,
                               app={"slug": app.slug, "title": app.title, "version": app.version},
                               timeout=spec.get("timeout_seconds", 30), services=AppServices(app))
    finally:
        event_depth.reset(token)
    async with apps_session() as session:
        session.add(AppFunctionRun(app_slug=slug, version=app.version, function=name, trigger=trigger,
                                   status=result.status, input_json=_clip(input), started_at=started_at,
                                   output_json=_clip(result.output) if result.ok else "",
                                   error=_clip(result.error), logs=_clip(result.logs),
                                   duration_ms=result.duration_ms))
        await session.flush()
        stale = select(AppFunctionRun.id).where(AppFunctionRun.app_slug == slug).order_by(
            AppFunctionRun.started_at.desc()).offset(RUNS_KEPT_PER_APP)
        await session.execute(delete(AppFunctionRun).where(AppFunctionRun.id.in_(stale)))
    if not result.ok:
        await record_error(slug, f"Function {name} failed ({trigger}): {result.error}",
                           "\n".join(part for part in (result.traceback, result.logs) if part))
    return result


async def recent_runs(slug: str, limit: int = 10) -> list[dict[str, Any]]:
    async with apps_session() as session:
        rows = (await session.execute(select(AppFunctionRun).where(AppFunctionRun.app_slug == slug)
                .order_by(AppFunctionRun.started_at.desc()).limit(limit))).scalars()
        return [{"function": row.function, "trigger": row.trigger, "status": row.status, "version": row.version,
                 "error": row.error, "started_at": row.started_at.isoformat(), "duration_ms": row.duration_ms}
                for row in rows]
