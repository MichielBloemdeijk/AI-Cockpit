"""Runs one app function in its own process; started by `function_host.py`.

Standard library only: it is started as `python -I -B function_runner.py`, so
nothing from the backend is importable here. Protocol over stdin/stdout, one
JSON object per line:

    host   -> runner  {"file", "input", "app", "trigger", "read_roots"}
    runner -> host    {"id", "call", "params"}             a platform call
    host   -> runner  {"id", "ok", "result" | "error"}
    runner -> host    {"done": true, "ok": true, "output"}  or  {"done": true, "ok": false, "error", "traceback"}

Before the app's code is imported, an audit hook blocks sockets, processes,
native libraries, file writes and reads outside the Python installation and the
app's functions folder. That stops mistakes, not deliberately hostile code
(docs/jarvis-tasks/13-app-platform.md): network, data and files go through the
platform calls, which enforce the app's grants.
"""
import base64
import contextlib
import importlib.util
import io
import json
import os
import sys
import traceback

BLOCKED_EVENTS = frozenset({
    "socket.connect", "socket.bind", "socket.getaddrinfo", "socket.gethostbyname", "socket.gethostbyaddr",
    "socket.sendto", "subprocess.Popen", "os.system", "os.startfile", "os.posix_spawn", "os.exec", "os.spawn",
    "os.fork", "os.kill", "ctypes.dlopen", "ctypes.dlsym", "os.remove", "os.rename", "os.rmdir", "os.mkdir",
    "os.chmod", "os.symlink", "os.link", "os.truncate", "os.utime", "shutil.rmtree", "shutil.copyfile",
    "shutil.move", "os.putenv", "os.unsetenv", "winreg.CreateKey", "winreg.SetValue", "winreg.DeleteKey",
    "winreg.DeleteValue", "_winapi.CreateProcess", "_winapi.CreatePipe", "_winapi.CreateNamedPipe",
})
WRITE_FLAGS = os.O_WRONLY | os.O_RDWR | os.O_APPEND | os.O_CREAT | os.O_TRUNC


class Blocked(PermissionError):
    pass


class CockpitError(Exception):
    """A platform call failed: invalid data, missing grant, network error."""


_protocol_in = sys.stdin.buffer
_protocol_out = sys.stdout.buffer
_next_id = 0


def _send(message):
    _protocol_out.write(json.dumps(message, separators=(",", ":")).encode() + b"\n")
    _protocol_out.flush()


def _call(method, params):
    global _next_id
    _next_id += 1
    _send({"id": _next_id, "call": method, "params": params})
    line = _protocol_in.readline()
    if not line:
        os._exit(3)  # the host is gone
    reply = json.loads(line)
    if not reply.get("ok"):
        raise CockpitError(reply.get("error") or "Platform call failed")
    return reply.get("result")


class Response:
    def __init__(self, reply):
        self.status = reply["status"]
        self.headers = reply["headers"]
        self.url = reply["url"]
        self.content = base64.b64decode(reply["body_base64"])

    @property
    def ok(self):
        return 200 <= self.status < 300

    @property
    def text(self):
        return self.content.decode("utf-8", errors="replace")

    def json(self):
        return json.loads(self.content)


class _Data:
    def get(self, collection, key):
        return _call("data.get", {"collection": collection, "key": key})

    def put(self, collection, key, value, expected_revision=None):
        return _call("data.put", {"collection": collection, "key": key, "value": value,
                                  "expected_revision": expected_revision})

    def delete(self, collection, key, expected_revision=None):
        return _call("data.delete", {"collection": collection, "key": key, "expected_revision": expected_revision})

    def query(self, collection, where=None, order=None, limit=50):
        return _call("data.query", {"collection": collection, "where": where, "order": order, "limit": limit})


class _Files:
    def list(self, prefix=None):
        return _call("files.list", {"prefix": prefix})

    def read(self, name):
        found = _call("files.read", {"name": name})
        return None if found is None else base64.b64decode(found["data_base64"])

    def read_text(self, name, encoding="utf-8"):
        data = self.read(name)
        return None if data is None else data.decode(encoding)

    def write(self, name, data, content_type=None):
        if isinstance(data, str):
            data = data.encode("utf-8")
            content_type = content_type or "text/plain"
        return _call("files.write", {"name": name, "content_type": content_type,
                                     "data_base64": base64.b64encode(bytes(data)).decode()})

    def delete(self, name):
        return _call("files.delete", {"name": name})


class _Workflows:
    def start(self, workflow, goal):
        """Start one of the app's workflows (a paid agent task); returns {"task_id", "status", "url"}."""
        return _call("workflows.start", {"workflow": workflow, "goal": goal})


class Cockpit:
    """The platform as a function sees it: `handle(input, cockpit)`."""

    def __init__(self, app, trigger):
        self.app = app
        self.trigger = trigger
        self.data = _Data()
        self.files = _Files()
        self.workflows = _Workflows()

    def fetch(self, url, method="GET", headers=None, body=None, json=None, timeout=20):
        if json is not None:
            body = _json_dumps(json)
            headers = {"Content-Type": "application/json", **(headers or {})}
        if isinstance(body, str):
            body = body.encode("utf-8")
        return Response(_call("fetch", {"url": url, "method": method, "headers": headers or {},
                                        "body_base64": base64.b64encode(body).decode() if body else None,
                                        "timeout": timeout}))

    def log(self, *values):
        print(*values)


def _json_dumps(value):
    return json.dumps(value)


def _guard(read_roots):
    roots = tuple(os.path.normcase(os.path.abspath(root)) + os.sep for root in read_roots)
    inside = [False]

    def hook(event, args):
        if inside[0]:
            return
        if event in BLOCKED_EVENTS or event.startswith("os.exec") or event.startswith("os.spawn"):
            raise Blocked(f"{event} is not available to app functions; use the cockpit API")
        if event != "open":
            return
        path, mode, flags = args
        if isinstance(path, int) or path is None:
            return
        writing = (isinstance(mode, str) and any(letter in mode for letter in "wax+")) or (
            isinstance(flags, int) and flags & WRITE_FLAGS)
        if writing:
            raise Blocked("App functions cannot write files; use cockpit.files")
        inside[0] = True
        try:
            target = os.path.normcase(os.path.abspath(os.fsdecode(path)))
        finally:
            inside[0] = False
        if not (target + os.sep).startswith(roots):
            raise Blocked("App functions can only read their own package; use cockpit.files")

    sys.addaudithook(hook)


def main():
    job = json.loads(_protocol_in.readline())
    folder = os.path.dirname(os.path.abspath(job["file"]))
    logs = io.StringIO()
    sys.stdout = sys.stderr = logs
    sys.path.insert(0, folder)
    _guard([sys.prefix, sys.base_prefix, sys.exec_prefix, *job["read_roots"]])
    try:
        with contextlib.redirect_stdout(logs):
            spec = importlib.util.spec_from_file_location("app_function", job["file"])
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
            output = module.handle(job["input"], Cockpit(job["app"], job["trigger"]))
        try:
            json.dumps(output)
        except (TypeError, ValueError) as exc:
            raise TypeError(f"handle returned a value that is not JSON: {exc}") from None
        _send({"done": True, "ok": True, "output": output, "logs": logs.getvalue()[-4000:]})
    except BaseException as exc:  # the app's own failure is reported, never raised
        _send({"done": True, "ok": False, "error": f"{type(exc).__name__}: {exc}"[:2000],
               "traceback": "".join(traceback.format_exception(exc))[-4000:], "logs": logs.getvalue()[-4000:]})


if __name__ == "__main__":
    main()
