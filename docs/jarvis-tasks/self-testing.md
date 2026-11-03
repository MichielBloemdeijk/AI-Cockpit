# Jarvis self-testing

Jarvis is tested by agents first; manual phone checks are a short final step.
Everything lives in `backend/app/selftest/`. Production code never imports it.
This guide covers the Jarvis voice layers; [VERIFICATION.md](../VERIFICATION.md)
lists every check in the repository and when to run it.

```powershell
cd backend
$env:PYTHONUTF8 = "1"
uv run python -m app.selftest doctor                                    # free diagnosis
uv run python -m app.selftest offline                                   # L0
uv run python -m app.selftest run --suite core --name <run> --live      # L2 browser
uv run python -m app.selftest run --suite core --name <run> --live --android   # L4 emulator
```

## Owners

- Code: `backend/app/selftest/` (`__main__` is the CLI, `host`, `doctor`, `replay`, `scenarios/`); production code never imports it
- Tests: `backend/tests/test_selftest.py`

## Layers

| Layer | What runs | Cost |
|---|---|---|
| L0 | A network-blocked Jarvis smoke slice: 14 backend test modules, two frontend modules, the browser media self-check, the driver syntax check, and the Android build and unit tests. Not the full suite. A run with `--skip` prints `"complete": false` | free |
| L1 | Replay of saved real conversations through the bookkeeper (`app.selftest.replay`) | Responses calls only |
| L2 | Scripted voice scenarios through the real browser/WebRTC client | paid voice |
| L3 | Adaptive simulated user (a second GPT-Live session) talking to Jarvis | paid voice |
| L4 | The same through the Android emulator client | paid voice |

## Diagnose before paying

`python -m app.selftest doctor` is free and read-only. It inspects the configured
database (or `--database <file>`, or a recorded run with `--run <folder>`) and
prints findings: symptom, component, evidence and a proposed fix. It exits 1 when
a finding is high severity. For a run folder it also writes `diagnosis.json`
there. It changes no application state. Run it before rerunning a paid scenario.

Evidence lives under `backend/data/selftest/<run>/` (one folder per case:
`events.jsonl`, the case database, `caller.wav`/`assistant.wav`, logs and the
request ledger).

## Replay inputs

L1 replays real recorded conversations listed in
`backend/app/selftest/replay_labels.yaml` with their agreed truth. The
recordings are private and kept outside Git under `backend/data/` (paths in the
labels file). A case whose recording is absent reports `missing_recording`; on
a fresh clone every case does, and that is not a regression.

## How a voice run works

- `run_case` starts an isolated backend (`app.selftest.host`, the unmodified
  production app with recording-only wrappers) and its own SQLite database under
  `data/selftest/<run>/`.
- A deterministic fixture worker finishes tasks at scenario-controlled moments.
- The browser driver (`frontend/e2e/announcement-eval.mjs`) or the Android driver
  (`android/scripts/verify-announcements.py`) speaks pre-rendered caller audio
  into the real microphone path.
- Both sides of the conversation are recorded (`caller.wav`, `assistant.wav`),
  together with provider events (`events.jsonl`), the database and logs.
- The harness then transcribes what the client actually played (whisper-1).

## Scenarios

Scenarios are YAML under `backend/app/selftest/scenarios/<suite>.yaml`.

- Each has an `id` and an `expect`: plain language describing what a good human
  assistant would do. The reviewing agent judges against it; it is not an exact
  transcript.
- Scripted scenarios also have `expectation` (`required`, `forbidden`, `optional`
  or `query`) and `steps`. Step types are listed in `app/selftest/scenarios/__init__.py`.

Add a scenario by appending to a suite. The L0 tests validate every suite.

## Allowances and stopping rules

- Each run directory has a connection allowance: 16 browser, 12 Android
  (`--max-connections` overrides it).
- Runs stop on billing, quota, auth or evidence-integrity failures.
- Nothing is retried automatically, and failed runs are kept.
- Personal `.env` settings (password access, secure cookies) are neutralised in
  the test setup and isolated hosts.
