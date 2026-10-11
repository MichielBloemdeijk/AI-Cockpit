"""One Windows host owns loopback services and their descendants. No credentials in state/logs."""
from __future__ import annotations
import json
import msvcrt
import os
from pathlib import Path
import shutil
import socket
import subprocess
import sys
import time
import urllib.request

from app.config import settings
from app.services.auth import create_session_token
from app.services.windows_job import contain_current_process_tree, process_creation_stamp, process_has_exited

ROOT = Path(__file__).resolve().parents[3]
STATE = ROOT / ".cockpit/jarvis/personal-host.json"
STOP = STATE.with_suffix(".stop")


def write_state(value):
    temporary = STATE.with_suffix(".new")
    temporary.write_text(json.dumps(value, indent=2), encoding="utf-8")
    temporary.replace(STATE)


def transport_check(config=settings):
    """The existing Serve listener must be tailnet HTTPS, not Funnel/public."""
    from urllib.parse import urlparse
    url = urlparse(config.jarvis_public_url)
    if url.scheme != "https" or not url.hostname or not url.hostname.endswith(".ts.net") or url.path not in {"", "/"} or url.port not in {None, 443}:
        raise RuntimeError("Set JARVIS_PUBLIC_URL to this host's existing Tailscale HTTPS address.")
    tailscale = shutil.which("tailscale")
    if not tailscale:
        raise RuntimeError("Tailscale is not installed or available.")
    serve = json.loads(subprocess.check_output([tailscale, "serve", "status", "--json"], text=True, timeout=15))
    own = json.loads(subprocess.check_output([tailscale, "status", "--json"], text=True, timeout=15))
    actual = own.get("Self", {}).get("DNSName", "").rstrip(".")
    if actual != url.hostname or own.get("BackendState") != "Running" or not own.get("Self", {}).get("Online"):
        raise RuntimeError("Connect Tailscale on this host and use its HTTPS name.")
    handler = serve.get("Web", {}).get(f"{url.hostname}:443", {}).get("Handlers", {}).get("/", {})
    if handler.get("Proxy") != "http://127.0.0.1:3000" or not serve.get("TCP", {}).get("443", {}).get("HTTPS") or any(serve.get("AllowFunnel", {}).values()):
        raise RuntimeError("Expected private Tailscale Serve HTTPS -> 127.0.0.1:3000 with Funnel off. Existing networking was not changed.")
    return {"public_url": config.jarvis_public_url.rstrip("/"), "tailscale_https": True, "funnel": False, "frontend_bind": "127.0.0.1:3000", "backend_bind": "127.0.0.1:8000"}


def health():
    request = urllib.request.Request("http://127.0.0.1:8000/api/jarvis/state",
        headers={"Cookie": "cockpit_session=" + create_session_token()})
    with urllib.request.urlopen(request, timeout=3) as response:
        value = json.load(response)
    with urllib.request.urlopen("http://127.0.0.1:3000/link-phone", timeout=3) as response:
        if response.status != 200:
            raise RuntimeError("Frontend is not ready")
    if value.get("worker_health", {}).get("status") != "online":
        raise RuntimeError("Worker is not online")


def listener_check(owner_pid=None):
    owner_pid = os.getpid() if owner_pid is None else owner_pid
    script = "Get-NetTCPConnection -State Listen -LocalPort 3000,8000 | Select-Object LocalAddress,LocalPort,OwningProcess | ConvertTo-Json -Compress"
    rows = json.loads(subprocess.check_output(["powershell", "-NoProfile", "-Command", script], text=True, timeout=15))
    if isinstance(rows, dict): rows = [rows]
    if {row["LocalPort"] for row in rows} != {3000, 8000} or any(row["LocalAddress"] != "127.0.0.1" for row in rows):
        raise RuntimeError("Service listener boundary is not loopback-only.")
    parents = json.loads(subprocess.check_output(["powershell", "-NoProfile", "-Command",
        "Get-CimInstance Win32_Process | Select-Object ProcessId,ParentProcessId | ConvertTo-Json -Compress"], text=True, timeout=15))
    parents = {row["ProcessId"]: row["ParentProcessId"] for row in parents}
    for row in rows:
        ancestor, visited = row["OwningProcess"], set()
        while ancestor != owner_pid and ancestor not in visited:
            visited.add(ancestor)
            ancestor = parents.get(ancestor, 0)
        if ancestor != owner_pid:
            raise RuntimeError("Listener is not owned by this Jarvis host.")
    return {"observed_loopback_only": True, "owned_listeners": True}


def status():
    try:
        value = json.loads(STATE.read_text(encoding="utf-8"))
        if process_has_exited(value["pid"], value["creation_stamp"]) is not False:
            return {"status": "Stopped"}
        if value.get("status") == "Ready":
            try:
                health()
                transport_check()
            except Exception:
                value["status"] = "Needs attention"
                value["message"] = "Check Tailscale and the service logs; a process alone does not prove readiness."
        return value
    except (OSError, ValueError, KeyError):
        return {"status": "Stopped"}


def stop():
    value = status()
    if value["status"] == "Stopped":
        print("Stopped")
        return
    STOP.touch()
    for _ in range(250):
        if process_has_exited(value["pid"], value["creation_stamp"]) is True:
            print("Stopped; task writers and descendants terminated. History and pairing retained.")
            return
        time.sleep(.2)
    raise RuntimeError("Shutdown is not confirmed. Do not start a second host; inspect logs.")


def serve():
    STATE.parent.mkdir(parents=True, exist_ok=True)
    with STATE.with_suffix(".lock").open("a+b") as lease:
        lease.seek(0); lease.write(b"0"); lease.flush(); lease.seek(0)
        try:
            msvcrt.locking(lease.fileno(), msvcrt.LK_NBLCK, 1)
        except OSError:
            raise RuntimeError("Jarvis already has a host. Use Status or Stop.") from None
        contain_current_process_tree()
        transport = transport_check()
        if len(settings.session_secret_key) < 32 or settings.session_secret_key == "dev-secret-change-me":
            raise RuntimeError("Set a persistent private SESSION_SECRET_KEY before linking a phone.")
        if settings.jarvis_access_mode == "password" and not settings.auth_password.get_secret_value():
            raise RuntimeError("Password mode requires AUTH_PASSWORD in private configuration.")
        if not settings.task_pool_enabled or settings.task_executor != "codex":
            raise RuntimeError("Personal host requires TASK_EXECUTOR=codex and TASK_POOL_ENABLED=true.")
        for port in (8000, 3000):
            with socket.socket() as probe:
                probe.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
                probe.bind(("127.0.0.1", port))
        node = shutil.which("node")
        if not node or not (ROOT / "frontend/.next/BUILD_ID").is_file():
            raise RuntimeError("Install frontend dependencies and run npm run build first.")
        STOP.unlink(missing_ok=True)
        state = {"status": "Starting", "pid": os.getpid(), "creation_stamp": process_creation_stamp(os.getpid()),
                 "access_mode": settings.jarvis_access_mode, "password_configured": bool(settings.auth_password.get_secret_value()),
                 "command_mode": settings.task_command_mode, "transport": transport}
        write_state(state)
        processes = []
        logs = STATE.parent / "logs"
        logs.mkdir(exist_ok=True)
        commands = [("backend", [sys.executable, "-m", "app.jarvis_tasks.personal_backend", str(STOP)], ROOT / "backend"),
                    ("frontend", [node, str(ROOT / "frontend/node_modules/next/dist/bin/next"), "start", "--hostname", "127.0.0.1", "--port", "3000"], ROOT / "frontend")]
        try:
            for name, command, cwd in commands:
                with (logs / f"personal-{name}.log").open("a", encoding="utf-8") as output:
                    process = subprocess.Popen(command, cwd=cwd, stdout=output, stderr=output)
                processes.append(process)
            deadline = time.monotonic() + 120
            while not STOP.exists():
                if any(process.poll() is not None for process in processes):
                    raise RuntimeError("A service exited; inspect the personal service logs.")
                try:
                    health()
                    if state["status"] != "Ready":
                        state["transport"].update(listener_check())
                        state["status"] = "Ready"
                        write_state(state)
                except Exception:
                    if state["status"] == "Ready":
                        state["status"] = "Needs attention"
                        write_state(state)
                    if time.monotonic() > deadline and state["status"] == "Starting":
                        raise RuntimeError("Services are not ready; inspect personal service logs.")
                time.sleep(.5 if state["status"] == "Starting" else 15)
        except Exception as error:
            write_state(state | {"status": "Needs attention", "message": str(error)})
            raise
        finally:
            STOP.touch()
            if processes:
                try: processes[0].wait(timeout=25)
                except subprocess.TimeoutExpired: processes[0].kill(); processes[0].wait(timeout=10)
            write_state(state | {"status": "Stopped"})
            # Job close on host exit kills Next and all remaining descendants.


if __name__ == "__main__":
    action = sys.argv[1] if len(sys.argv) > 1 else "status"
    try:
        if action == "serve": serve()
        elif action == "stop": stop()
        elif action == "status": print(json.dumps(status(), indent=2))
        elif action == "check": print(json.dumps(transport_check(), indent=2))
        else: raise RuntimeError("Use serve, stop, status or check")
    except Exception as error:
        print(str(error), file=sys.stderr)
        sys.exit(1)
