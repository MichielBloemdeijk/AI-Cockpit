"""Production-build verification for generated apps."""
from __future__ import annotations

import asyncio
import os
from pathlib import Path
import re
import shutil
import subprocess
from uuid import uuid4

from app.config import settings
from app.services.app_registry import app_registry_service, resolve_generated_app_contract


BUILD_TIMEOUT_SECONDS = 180
MAX_BUILD_OUTPUT_CHARS = 12_000
_ANSI_ESCAPE_RE = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]")
_BUILD_LOCK = asyncio.Lock()


class AppVerificationInfrastructureError(RuntimeError):
    """Verification could not run, rather than the generated app failing its build."""

    fatal = True


def _repo_root() -> Path:
    return settings.backend_root.parent.resolve()


def _clean_build_output(output: str) -> str:
    cleaned = _ANSI_ESCAPE_RE.sub("", str(output or "")).strip()
    if len(cleaned) <= MAX_BUILD_OUTPUT_CHARS:
        return cleaned
    return "...[earlier build output omitted]\n" + cleaned[-MAX_BUILD_OUTPUT_CHARS:]


async def _run_frontend_build(frontend_root: Path) -> tuple[int, str]:
    npm_executable = shutil.which("npm.cmd") or shutil.which("npm")
    if not npm_executable:
        raise AppVerificationInfrastructureError("Generated app verification requires npm on PATH")

    verification_id = uuid4().hex
    dist_dir_name = f".next-verify-{verification_id}"
    tsconfig_name = f".next-verify-{verification_id}.tsconfig.json"
    dist_dir = (frontend_root / dist_dir_name).resolve()
    tsconfig_path = (frontend_root / tsconfig_name).resolve()
    if dist_dir.parent != frontend_root.resolve() or not dist_dir.name.startswith(".next-verify-"):
        raise ValueError("Refusing to use an unsafe generated-app verification directory")
    if tsconfig_path.parent != frontend_root.resolve() or not tsconfig_path.name.startswith(".next-verify-"):
        raise ValueError("Refusing to use an unsafe generated-app verification config")

    source_tsconfig = frontend_root / "tsconfig.json"
    if not source_tsconfig.is_file():
        raise AppVerificationInfrastructureError("Generated app verification requires frontend/tsconfig.json")
    shutil.copyfile(source_tsconfig, tsconfig_path)

    environment = os.environ.copy()
    environment["AI_COCKPIT_NEXT_DIST_DIR"] = dist_dir_name
    environment["AI_COCKPIT_TSCONFIG_PATH"] = tsconfig_name
    environment["NEXT_TELEMETRY_DISABLED"] = "1"

    def _run_build() -> tuple[int, str]:
        try:
            completed = subprocess.run(
                [npm_executable, "run", "build"],
                cwd=frontend_root,
                env=environment,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                timeout=BUILD_TIMEOUT_SECONDS,
                check=False,
            )
        except subprocess.TimeoutExpired as exc:
            raise AppVerificationInfrastructureError(
                f"Generated app verification timed out after {BUILD_TIMEOUT_SECONDS} seconds"
            ) from exc
        except OSError as exc:
            raise AppVerificationInfrastructureError(
                f"Generated app verification could not start npm: {exc}"
            ) from exc
        return int(completed.returncode), _clean_build_output(completed.stdout.decode(errors="replace"))

    try:
        async with _BUILD_LOCK:
            returncode, build_output = await asyncio.to_thread(_run_build)
    finally:
        shutil.rmtree(dist_dir, ignore_errors=True)
        tsconfig_path.unlink(missing_ok=True)

    return returncode, build_output


async def verify_generated_app(app_slug: str) -> dict[str, str]:
    normalized_slug = str(app_slug or "").strip()
    if not normalized_slug:
        raise ValueError("Generated app verification requires app_slug")

    record = await app_registry_service.get_app_by_slug(normalized_slug)
    if record is None:
        raise ValueError(f"Generated app not found: {normalized_slug}")

    contract = resolve_generated_app_contract(record.slug, record.manifest_json)
    app_root = _repo_root() / contract.frontend_root
    required_files = [app_root / "page.tsx", app_root / "layout.tsx"]
    missing_files = [path.relative_to(_repo_root()).as_posix() for path in required_files if not path.is_file()]
    if missing_files:
        error = "Generated app is missing required files: " + ", ".join(missing_files)
        await app_registry_service.update_app(
            record.id,
            status="building",
            verification_status="failed",
            last_error=error,
        )
        raise ValueError(error)

    record = await app_registry_service.update_app(
        record.id,
        status="building",
        verification_status="running",
        last_error=None,
    )
    if record is None:
        raise ValueError(f"Generated app disappeared during verification: {normalized_slug}")

    source_error = app_registry_service.get_display_error(record)
    if source_error:
        await app_registry_service.update_app(
            record.id,
            status="building",
            verification_status="failed",
            last_error=source_error,
        )
        raise ValueError(source_error)

    frontend_root = _repo_root() / "frontend"
    try:
        returncode, build_output = await _run_frontend_build(frontend_root)
    except AppVerificationInfrastructureError:
        await app_registry_service.update_app(
            record.id,
            status="ready_for_test",
            verification_status="not_started",
            last_error=None,
        )
        raise
    except Exception as exc:
        error = str(exc).strip() or exc.__class__.__name__
        await app_registry_service.update_app(
            record.id,
            status="ready_for_test",
            verification_status="not_started",
            last_error=None,
        )
        raise AppVerificationInfrastructureError(
            f"Generated app verification failed unexpectedly: {error}"
        ) from exc

    if returncode != 0:
        error = build_output or f"Next.js build failed with exit code {returncode}"
        await app_registry_service.update_app(
            record.id,
            status="building",
            verification_status="failed",
            last_error=error,
        )
        raise ValueError(error)

    await app_registry_service.update_app(
        record.id,
        status="verified",
        verification_status="passed",
        last_error=None,
    )
    return {
        "app_id": record.id,
        "slug": record.slug,
        "route_path": record.route_path,
        "status": "verified",
        "verification_status": "passed",
    }


__all__ = ["AppVerificationInfrastructureError", "verify_generated_app"]
