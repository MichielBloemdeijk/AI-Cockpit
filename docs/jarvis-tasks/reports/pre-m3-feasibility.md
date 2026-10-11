# Pre-M3 feasibility — 2026-09-30

Historical outcome: the selected architecture remained viable for proceeding
with M3's background implementation. No phone or user interaction was required.
The later [closure roadmap](../00-implementation-roadmap.md) assigns production
capture/audio integration to M8 and deferred physical acceptance to M11; older
M5 references below describe the schedule at this feasibility checkpoint.

## Native capture seam

Run from `backend`:

```powershell
uv run python ../android/scripts/verify-capture-feasibility.py
```

The full API-37 Google APIs revision-6 emulator uses the production invocation
method called after an accepted local wake. This tests the invocation boundary,
not microphone acoustics or the ONNX detector. No injected bitmap, MediaProjection,
system-gesture substitution or compatibility bypass is used.

The [Android-17 invocation enforcement](https://android.googlesource.com/platform/frameworks/base/+/refs/heads/android17-release/services/voiceinteraction/java/com/android/server/voiceinteraction/VoiceInteractionManagerService.java)
and [assist-data request path](https://android.googlesource.com/platform/frameworks/base/+/refs/heads/android17-release/services/core/java/com/android/server/am/AssistDataRequester.java)
explain the observed foreground check, required basic context and absence of
callbacks when no visible app activity is available. The harness wakes and
dismisses a non-secure leftover keyguard before beginning each run.

- Background invocation loses screenshot flags and fails closed, even with
  context opt-in. Once the assistant window is drawn, foreground requests work.
- Explicit fixture opt-in enables required assist-data context. The current
  app opt-out remains unchanged and unavailable; this exposed an integration
  coupling, not a reason to override privacy settings.
- Real screen A/blue and fresh screen B/red bytes reach private run-bound
  uploads under the same root activation, Live session and generation. A pixel
  check near the bottom also rejects an accidentally captured assistant sheet.
- Protected content, Android screenshot disablement, screen-context app-op
  denial and secure keyguard are tested separately. Each rejects admission
  without a task/context request. Protected app content can be black with
  visible system chrome rather than a null callback.

Fixes from the pass: terminal capture observations may attach to Live later
without resurrecting pixels/changing lifetime; fresh requests wait across a
render boundary after hiding assistant content; conservatively black content
is unavailable before encoding or upload. The black-content policy can reject
legitimate black screens and is not a universal protection detector.

Fresh admission uses the existing shared capture/task services directly in an
isolated helper process. The M2 Live gateway remains activation-only. Remaining
Phase-3 work is agent-testable: stage the *initial* wake capture after foreground
visibility, decouple basic context from optional rich structure, wire fresh Live
requests, and complete lifecycle/expiry/replacement tests. Do not enable the
production custom-wake screen route before these gates pass.

## M3 runtime/shared-service seam

```powershell
uv run pytest tests/test_m3_preflight.py -q
$env:JARVIS_LIVE_CODEX='1'
uv run pytest tests/test_m3_preflight.py -q
```

A blocked synchronous host is isolated off the event loop. Eight occupied
workers do not block new admission/cancellation, factual completion projection,
or history. Both HTTP task APIs and the direct Live shell dispatch/control
gateway are exercised without provider reasoning. A fresh dedicated API profile runs actual Luna/low Codex and the
M0 restricted MCP probe independently of those slots. The allowlisted operation
commits durably, and adversarial native shell/edit/web/subagent and undeclared
tool requests produce no forbidden operation.

This is feasibility, not M3 acceptance: the durable decision queue, feedback
cursor, orchestration-specific tools, restart reconciliation, stale-action
fences and background answer/recovery cases are still to be implemented.
The single-run service timing is not a production p95 measurement.

## Deferred external gates

Emulators cannot establish real microphone acoustics, OEM-specific behavior,
physical battery performance or real-world provider latency. Keep those at M5.
Subscription execution still requires dedicated-profile continuation evidence.
API hard-budget enforcement is not proven by availability/error-classification
fixtures; verify the account cutoff before unattended production rollout.

Redacted native and runtime reports are retained beside this file. Full logs,
database and private image evidence remain in the harness's printed temp folder,
not Git or application telemetry.

The post-fix M2 end-to-end regression also passes: real screenshot → supervised
Codex → maintained result → notification tap/WebView → process reconnect with
one execution attempt. Targeted backend regressions and Android JVM/release
build checks pass; none of this establishes production provider p95 timing.
