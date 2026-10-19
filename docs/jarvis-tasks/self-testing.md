# Jarvis self-testing

Jarvis is tested by agents first; manual phone checks are a short final step.
Everything lives in `backend/app/selftest/`. Production code never imports it.

```powershell
cd backend
$env:PYTHONUTF8 = "1"
uv run python -m app.selftest offline                                   # L0
uv run python -m app.selftest run --suite core --name <run> --live      # L2 browser
uv run python -m app.selftest run --suite core --name <run> --live --android   # L4 emulator
```

## Layers

| Layer | What runs | Cost |
|---|---|---|
| L0 | Network-blocked backend, frontend and Android unit checks, the browser media self-check and the driver syntax check | free |
| L1 | Replay of saved real conversations through the bookkeeper (`app.selftest.replay`) | Responses calls only |
| L2 | Scripted voice scenarios through the real browser/WebRTC client | paid voice |
| L3 | Adaptive simulated user (a second GPT-Live session) talking to Jarvis | paid voice |
| L4 | The same through the Android emulator client | paid voice |

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
  or `query`) and `steps`. Step types are listed in `scenarios/__init__.py`.

Add a scenario by appending to a suite. The L0 tests validate every suite.

## Allowances and stopping rules

- Each run directory has a connection allowance: 16 browser, 12 Android
  (`--max-connections` overrides it).
- Runs stop on billing, quota, auth or evidence-integrity failures.
- Nothing is retried automatically, and failed runs are kept.
- Personal `.env` settings (password access, secure cookies) are neutralised in
  the test setup and isolated hosts.
