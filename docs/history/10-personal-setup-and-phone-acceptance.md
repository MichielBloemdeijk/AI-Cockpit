# Simple personal setup and final phone acceptance

Status: **M11A–D completed and personal use accepted on 2026-10-11.**
The user confirmed working tasks, including a recipe created from a screenshot,
and accepted all work as complete. See the [final acceptance record](reports/personal-use-accepted-2026-10-11.md).
This completed plan retains setup contracts and recorded A–C checks; the
[completion ledger](00-implementation-roadmap.md) owns current status.

## Agreed scope

Use the existing Windows host and Tailscale connection. No new hosting platform,
public deployment or account system is required. A stable phone-facing URL and
reliable startup are implemented for this personal setup. Keep one authoritative
worker host at a time; laptop/PC failover is not a new requirement.

An explicit personal-use mode may operate without a Jarvis password. Verify once
that the actual listener/proxy arrangement restricts access to the intended
local/Tailscale interfaces; using a Tailscale URL alone is not that check. An
optional password may be plaintext in a private, ignored local configuration
file. Jarvis handles verification internally; the user does not generate hashes.
Passwords and permanent credentials never enter links, logs, reports or committed
configuration. A local password offers limited protection from someone already
using the unlocked host.

Keep signed sessions, origin protections, device identity/revocation, lock and
screenshot privacy, task ownership, journaling, leases and cancellation. Paired
phone web access must retain device permissions rather than silently grant a
broader owner session. Keep the persistent dedicated subscription profile and
explicitly selected trusted command mode.

A full deployment backup is optional for disposable development data. Offer a
convenient snapshot when the user wants to retain useful tasks/results. Persistence,
migration, recovery and confirmed writer shutdown still need testing. Source,
credentials, account settings and unrelated files are never disposable.

M10 passed on source c8a0aec7 with four accepted M8 caveats. This remains baseline
evidence, not proof of later executable changes. Native sandbox repair, real connector
integrations, browser automation and waived bulk audio/performance cohorts are
outside this completion work.

## M11A — Personal access and startup (agent implementation)

- [x] Add explicit personal-use configuration with an optional password. With a
  password configured, require it for new owner browser sessions; without one,
  remove the artificial login form. Keep internal sessions and authorization
  boundaries; do not treat arbitrary credentials or origins as valid.
- [x] Accept the optional password directly in private configuration, with one
  clear configuration path, useful errors and credential-safe logging.
- [x] Update readiness checks, test profiles and documentation: personal mode
  does not fail solely because a password or full backup is absent. Record the
  access/execution mode and actual endpoint restrictions truthfully.
- [x] Provide one start/stop action for frontend, backend and worker, showing
  the phone URL and Ready/Starting/Needs attention status. Preserve the signing
  secret and device credentials through ordinary restarts.
- [x] Reuse the existing host/address where possible. Verify clean restart,
  no duplicate workers, retained history and safe handling of uncertain work.
  Startup at Windows login is optional, not a completion prerequisite.

Exit: the agent can start/stop a coherent personal setup, with both password
options, without manual hash work or false readiness failures. Apply host/account
changes only within the user's selected personal-use scope.

## M11B — Link once and use the whole phone experience (agent implementation)

- [x] Add one **Link phone** action showing a QR code, an open-on-phone link and
  manual address/code fallback. Use the reachable address and a short-lived,
  single-use pairing code; never encode the permanent device credential.
- [x] Handle the link in Android so the user does not retype the address/code.
  Explain installation if the app is missing; system-camera/link use should
  avoid requiring a separate scanner setup.
- [x] Reuse saved, revocable device credentials. Ordinary updates, app/host
  restarts and Tailscale reconnects must not require pairing again. Give expired
  links, revoked devices and app-data reset a simple recovery action. Show linked
  devices and an Unlink action.
- [x] Open Jarvis and results without a second WebView login, including after
  session expiry or restart. Preserve device scope/lock restrictions, restrict
  credential use to the configured server and never forward credentials to
  external source links.
- [x] Opening a valid link pairs automatically and starts one guided flow for microphone,
  notifications, assistant and screenshot access. Per the user's 2026-10-10 decision,
  enable supported wake/boot/assist features together; keep pause and unlink under advanced settings. Android's
  genuine one-time system prompts remain; ordinary use needs no repeated approvals.

Exit: install, scan/open link, complete Android's required choices, then use
voice, notifications and result pages without another Jarvis login.

## M11C — Finish agent verification before the phone (agent verification)

- [x] Local/mock checks first: both password modes; invalid credentials; expired
  or replayed pairing; wrong server; revocation; session refresh; origin/device/
  lock boundaries; retained pairing; startup/stop; no duplicate workers or replay
  of uncertain effects.
- [x] Browser/emulator setup from an unpaired app through QR/link handling,
  result access and same-task follow-up. Exercise malformed links, interrupted
  setup, reconnect and error recovery without paid models where possible.
- [x] After local checks pass, use a small live pilot only for a changed provider
  seam if needed. Freeze the final executable candidate and complete required
  combined acceptance once. Reuse valid same-candidate evidence and retain failures
  and the four M8 exceptions. Keep the single final ready admission below 4.5 seconds;
  no new p95 requirement or bulk speech cohort is imposed.
- [x] Prepare one installable build, working Link phone flow, short checklist and
  correlated diagnostics. Mark **ready for the user's phone test** only when
  required automated checks pass, listing known caveats.

Automated proof: [personal setup completion report](reports/personal-setup-2026-10-09.md).
Final admission 3676.2937 ms; all 15 required assertions pass with four accepted M8 caveats.
The [phone guide](phone-test-checklist.md) remains available for setup and troubleshooting.

No phone testing is needed from the user during A–C. No new subscription login is
needed unless the supported runtime reports an actual authentication failure.
Report live request purposes/counts and usage; unavailable cost remains unknown.

## M11D — User's final phone acceptance (completed)

- [x] The user confirms that tasks work in the current Jarvis version.
- [x] Screenshot-based recipe creation works in the user's phone experience.
- [x] The user explicitly considers all work completed and accepts personal use.

The [acceptance record](reports/personal-use-accepted-2026-10-11.md) closes the
phase as a whole. The user did not provide individual observations for every
former checklist item; do not manufacture physical pass counts, hardware
identity or battery measurements. The checklist now serves as an optional
[phone guide](phone-test-checklist.md).

Accepted M8 caveats and trusted Windows command mode carry forward. Public or
multi-user deployment and additional integrations remain separate future scope.

## Completed delivery order

M11A → M11B → M11C → M11D is complete for the selected Windows/Tailscale host
and paired phone. There is no outstanding phone test or normal-use approval in
this plan. Preserve pairing, credentials and durable task/privacy guarantees
during future maintenance.
