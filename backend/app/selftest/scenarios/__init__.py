"""Declarative self-test scenarios, one YAML file per suite."""
from __future__ import annotations

from pathlib import Path

import yaml

FOLDER = Path(__file__).parent
STEP_TYPES = {"speak", "speak_async", "join", "admit", "settle", "wait", "wait_input", "wait_output",
    "at_caller_offset", "finish", "sync", "question", "eligible", "observation_end", "status_query",
    "disconnect", "resume", "reconnect", "observer_stop", "observer_restart", "opening", "comparison"}
SPOKEN_STEPS = {"speak", "speak_async", "status_query"}
EXPECTATIONS = {"required", "forbidden", "optional", "query"}


def validate(scenario: dict) -> dict:
    missing = {"id", "expect"} - set(scenario)
    if missing:
        raise ValueError(f"Scenario {scenario.get('id')} lacks {sorted(missing)}")
    if "adaptive" in scenario:
        if not str(scenario["adaptive"].get("agenda", "")).strip():
            raise ValueError(f"Adaptive scenario {scenario['id']} needs an agenda")
        return scenario
    if scenario.get("expectation") not in EXPECTATIONS:
        raise ValueError(f"Scenario {scenario['id']} needs expectation in {sorted(EXPECTATIONS)}")
    for step in scenario.get("steps") or []:
        if step.get("type") not in STEP_TYPES:
            raise ValueError(f"Scenario {scenario['id']} has unknown step {step.get('type')}")
        if step["type"] in SPOKEN_STEPS and not str(step.get("text", "")).strip():
            raise ValueError(f"Scenario {scenario['id']} has a spoken step without text")
    if not scenario.get("steps"):
        raise ValueError(f"Scenario {scenario['id']} has no steps")
    scenario.setdefault("observation_tail_ms", 30000)
    return scenario


def load_suite(name: str) -> list[dict]:
    path = FOLDER / f"{name}.yaml"
    if not path.is_file():
        raise FileNotFoundError(f"No scenario suite {name!r} in {FOLDER}")
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    scenarios = [validate(dict(s)) for s in data["scenarios"]]
    ids = [s["id"] for s in scenarios]
    if len(ids) != len(set(ids)):
        raise ValueError(f"Duplicate scenario ids in suite {name}")
    return scenarios


def speech_texts(scenarios: list[dict]) -> list[str]:
    texts = set()
    for scenario in scenarios:
        if scenario.get("opening"):
            texts.add(scenario["opening"])
        for step in scenario.get("steps") or []:
            if step["type"] in SPOKEN_STEPS:
                texts.add(step["text"])
            if step["type"] == "admit" and step.get("answer"):
                texts.add(step["answer"])
    return sorted(texts)
