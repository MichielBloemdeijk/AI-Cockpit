# Personal setup completed through automated acceptance

Updated 2026-10-10. **M11A-C complete; ready for the user's phone test (M11D).**
The selected private Windows/Tailscale setup is running and reports Ready. All
15 required final-candidate assertions pass, with four previously accepted M8
exceptions. No further subscription login is required.

## Implemented

Explicit personal/password access, optional private plaintext password,
constant-time checks, signed owner/device sessions and allowed-origin writes.
Removed hash login and arbitrary development passwords; nonempty obsolete hash
configuration fails closed. Password mode without a password also fails closed.

Settings offers Link phone with QR, open link and manual fallback. Codes expire
after five minutes and consume atomically once. Android handles the deep link,
retains its encrypted revocable identity, renews a scoped WebView session and
provides unlink/reconnect guidance. Failed pairing preserves the existing
connection; external source links receive no credentials. Device cookies never
become owner/pool sessions or receive capture-upload tokens; locked writes remain
blocked. Android permissions/assistant/screen choices are guided, with background
and boot listening optional.

`Jarvis.cmd` starts/stops frontend, backend and the supervised pool together.
Ready checks owned loopback listeners, real service/pool health, private Tailscale
HTTPS proxy and Funnel off. Singleton locking, exact process identity, durable
leases and Job Object descendant shutdown prevent competing writers. No mandatory
local LLM, new hosting, full backup or Windows-login startup was added.

Private configuration now selects personal access, Codex subscription-first
execution and reviewed workflows. Signing secret, keys, dedicated account profile
and all three completed task histories were preserved. No main data reset was
needed; only the disposable emulator app was cleared during unpaired setup checks.

## Final evidence

Source: `4f58a725e5f9e835d0b952c6a8c9664d4b9473fe10a170e3cdfc390e6c31fecc`.
SDK: 0.155.1. Main development schema remains migration 0037.

- `data/m11-final/final/acceptance.json`: passed, implementation_ready true;
  **15 required passed, four explicit accepted M8 exceptions, no required skips**.
- `data/m11-final/verified/`: **679 backend, 192 frontend, 30 Android** tests,
  frontend types/build/result export, real browser voice, coding/restart,
  actual subscription/API handoff, migration and real mobile result journey.
- Final ready admission: **3676.2937 ms**, speech end to committed admission
  received by client, zero failures. Proof: `ready-verified/audio/acceptance.json`.
  Single-final policy below 4500 ms; no p95 claim.
- Mobile journey admission: **3275.5216 ms**; both screen roles, three sources,
  real notification/reopen, no extra work on reopen, same-task UI continuation.
- `privacy-corrected/acceptance.json`: all 11 native privacy observations and
  11 capture-collection/upload contract tests passed; zero models.
- `process-tree-corrected/acceptance.json`: three termination tests passed,
  exact PID identity and zero writer growth after stop; zero models.
- `personal-browser-final.json`: QR/landing, device scope, revocation and host
  restart retaining phone session/history; zero models.
- `personal-emulator.json`: API37 unpaired link/interrupted setup, cookie loss
  renewal, notification tap, save/reopen, backlinks, external browser; zero models.
- `readiness-final.json`: all eight preparation checks passed, actual private
  transport confirmed. Physical/OEM and normal-use confirmation remain deferred;
  `ready_for_enablement=false` records that final user acceptance, not an app fault.

The normal host is Ready at https://sarah.tail68fb8f.ts.net. A repeated Start
reuses the same host, and the APK download returned HTTP200 with the expected
filename and size. [Phone checklist](../phone-test-checklist.md).

## Retained failures and paid-test discipline

Broad offline initially passed 665 cases, skipped two opt-in coding/subscription
checks and exposed one mock depending on a real key. That mock now uses a dummy
key and blocks network connections. Focused 50 checks and six final boundary
checks passed before live work.

Retained earlier failures include the initial controls/auth startup race, emulator
fixture origin403, missing fixture event idempotency, runner working directory
and captured Windows launcher handles. Corrections were verified locally.

The first combined attempt was stopped before browser/mobile paid stages because
a browser fixture expected a password form despite a valid retained session. The
corrected complete browser journey passed model-free, then the corrected frozen
candidate was verified. Raw partial output and `combined/aborted.json` remain.

The complete corrected run passed substantive stages but initially lacked valid
capture/privacy and process-tree proofs: plain HTTP contract tests inherited
Secure cookies, and process-tree flags did not match its declared isolated profile.
These two stages were rerun with the existing manifest's declared settings, without
source or host-configuration changes and without models. Their valid same-candidate
proofs were assembled with the passing full-run assertions; no paid suite was
repeated for those configuration corrections. Raw failures remain unchanged.

Paid purposes/counts: three targeted admission sessions across verification-source
corrections (4110.6379 / 3614.6128 / final 3676.2937 ms); two coding-stage and two
subscription/handoff-stage attempts; 11 provider cases in each backend attempt;
one browser Live session and one mobile workflow journey with two durable task
runs. Test/session counts are not HTTP/model-call totals: workers may make multiple
calls internally. All other setup, profile corrections and final assembly/readiness
checks used zero models. No broad speech/model sweep, new TTS, real exhaustion
spending or connector action was added.

Recorded selector usage across the three admissions: **11,271 input / 382 output
tokens**. Final: 3,769 input (2,923 cached) / 151 output, actual priority tier,
no fallback. These exclude unreported audio/runtime usage. Costs and the exact
aggregate provider request count remain unknown; no invoice estimate is claimed.

## Remaining acceptance and known limits

Only M11D physical phone testing and normal-use confirmation remain in this plan.
Install/link once, then check real speech/screens, results/controls, background/
lock behavior and restart/reconnect using the short checklist. The current APK
supports Android12+; automated acceptance uses API37.

Trusted Windows commands inherit Windows-user access; brokered edits remain
path-constrained and cancellation/leases remain active. Native Python sandbox
compatibility remains gated. Four M8 exceptions retain imperfect recognition/
startup greeting and unproven full speech/cold/fresh/capture-performance cohorts.
Physical acoustics, OEM behavior and battery/heat remain unproven until phone
checks; the short session still does not establish all-day battery life.
Public/multi-user deployment and actual connectors remain separate future scope.

Final APK SHA256: `f248b27e99f67bceb02353f2214521485dbfe35568424b7e8a77f3e020b78737`; 154,584,888 bytes.
