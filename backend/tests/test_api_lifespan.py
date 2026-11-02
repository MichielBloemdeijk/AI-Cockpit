from __future__ import annotations

import pytest

import app.main as main_module


class _Component:
    def __init__(self, name: str, trace: list[str], *, fail_start: bool = False, fail_stop: bool = False) -> None:
        self.name = name
        self.trace = trace
        self.fail_start = fail_start
        self.fail_stop = fail_stop

    async def start(self, **_kwargs) -> None:
        self.trace.append(f"start:{self.name}")
        if self.fail_start:
            raise RuntimeError(f"{self.name} failed to start")

    async def stop(self) -> None:
        self.trace.append(f"stop:{self.name}")
        if self.fail_stop:
            raise RuntimeError(f"{self.name} failed to stop")

    async def shutdown(self) -> None:
        await self.stop()


def _install(monkeypatch, *, fail_start: str | None = None, fail_stop: str | None = None) -> list[str]:
    trace: list[str] = []
    names = {
        "app_runtime": "app_runtime",
        "run_supervisor": "run_supervisor",
        "jarvis_live_telemetry": "telemetry",
        "openai_live_adapter": "live_adapter",
        "reminder_scheduler": "reminders",
        "jarvis_coordinator": "coordinator",
        "conversation_observer": "observer",
        "runtime_health_service": "health",
    }
    for attribute, name in names.items():
        monkeypatch.setattr(main_module, attribute, _Component(
            name, trace, fail_start=name == fail_start, fail_stop=name == fail_stop))

    async def close_engine() -> None:
        trace.append("close:engine")

    async def close_apps_engine() -> None:
        trace.append("close:apps_engine")

    async def noop() -> None:
        return None

    monkeypatch.setattr(main_module, "close_engine", close_engine)
    monkeypatch.setattr(main_module, "close_apps_engine", close_apps_engine)
    monkeypatch.setattr(main_module, "ensure_database_ready", noop)
    monkeypatch.setattr(main_module, "run_migrations", lambda: None)
    monkeypatch.setattr(main_module, "initialize_engine", lambda _url: None)
    monkeypatch.setattr(main_module.settings, "task_orchestration_enabled", False)
    monkeypatch.setattr(main_module.settings, "task_pool_enabled", False)
    return trace


@pytest.mark.asyncio
async def test_startup_failure_releases_started_components_in_reverse_order(monkeypatch) -> None:
    trace = _install(monkeypatch, fail_start="observer")
    with pytest.raises(RuntimeError, match="observer failed to start"):
        async with main_module.lifespan(main_module.app):
            pytest.fail("startup should not complete")
    assert trace == [
        "start:telemetry", "start:reminders", "start:coordinator", "start:observer",
        "stop:coordinator", "stop:reminders", "stop:live_adapter", "stop:telemetry",
        "stop:run_supervisor", "stop:app_runtime", "close:apps_engine", "close:engine",
    ]
    assert main_module.app.state.acceptance_runtime is None


@pytest.mark.asyncio
async def test_failing_release_does_not_skip_later_releases(monkeypatch) -> None:
    trace = _install(monkeypatch, fail_stop="coordinator")
    with pytest.raises(RuntimeError, match="coordinator failed to stop"):
        async with main_module.lifespan(main_module.app):
            trace.append("serving")
    assert trace[trace.index("serving"):] == [
        "serving", "stop:health", "stop:observer", "stop:coordinator", "stop:reminders",
        "stop:live_adapter", "stop:telemetry", "stop:run_supervisor", "stop:app_runtime",
        "close:apps_engine", "close:engine",
    ]
