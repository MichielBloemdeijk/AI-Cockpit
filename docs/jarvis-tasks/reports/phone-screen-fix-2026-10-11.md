# Paired browser voice screen capture repair — 2026-10-11

## Observed failure

The user's recipe request failed twice at 11:50 Amsterdam time (09:50 UTC).
Session `72cf1641-29a0-4a76-b479-2bead43967cb`, generation 17, had a
`jarvis-browser-…` controller rather than its authenticated phone identity.
Both `start_workflow` intents failed with `Fresh capture session is unavailable`.
There were no capture requests or Android capture reports in the main database.
The task was rejected before any screenshot callback or worker execution, so
these observations do not show Chrome denying screenshots.

Native screenshot refresh previously listened only to the native assistant's
own Live call. Paired WebView voice uses the browser Live controller; giving
Android permissions alone could not connect those two paths. A repeated selector
call also hit the existing intent argument-conflict fence after the first failure;
that separate observation is retained, not treated as a screenshot policy error.

## Repair

Authenticated phone calls derive their device controller identity from the bearer
or signed phone cookie. Ordinary desktop browser calls retain their browser
identity and cannot choose an arbitrary capture phone. Browser controller UI
recognizes its accepted session/generation even when the server supplies a device
controller identity.

Paired browser calls receive a durable, targeted activation capture request;
delegated turns receive the existing deduplicated fresh capture request with an
explicit phone-browser transport. Browser events remain redacted. The native
bearer event stream validates the current session, generation, activation, device
and expiry before a serialized capture-only assistant invocation. It neither
connects another provider call nor allocates a native audio controller. Android
collects the actual foreground screen through its screenshot callback, and the
transparent assistant closes afterward.

Captures remain memory-only, expire after 90 seconds and are purged on lock or
activation replacement. Server capture identity and revisions remain immutable.
Uploads still require the native device credential and an exact run-bound request.
Invocation and fresh images retain distinct roles. Android screenshot settings
and protected-app policy are unchanged. No MediaProjection, Accessibility service,
permission bypass, schema reset or additional provider spending is introduced.

## Targeted verification

- 21 backend checks pass: paired browser device binding, native-only capture
  reports, redacted browser events, other-device rejection, supersession fences,
  capture collection/upload contracts, authentication and personal setup.
- 193 frontend checks pass; 32 Android unit checks and the APK build pass.
- `data/phone-screen-fix/emulator-final/report.json` passes on API37: actual normal
  Chrome invocation capture, a changed Chrome page, a recipe task admitting both
  frozen invocation/latest roles and receiving both uploads, replay deduplication,
  one unchanged fake voice call, and Android-disabled screenshots refusing capture.
  Uploaded blue/red page pixels are checked, not just metadata or wrapper success.
- All tests call zero models. API keys are empty in the disposable capture backend.
  Paid speech recognition and recipe generation are not claimed by this run.

The native assistant regression also passes all eleven actual Android observations:
background/visible capture, fresh A/B screens, structure consent independence,
one voice session, and protected/disabled/denied/locked rejection. Its raw wrapper
report is retained as **failed** because its subsequent contract subprocess did
not inherit the profile's `SESSION_COOKIE_SECURE=false`; HTTP test cookies were
therefore withheld and pairing-code reads failed before capture tests. The same
eleven capture/collection contracts pass within the separate 21-test run with
the declared HTTP test setting (`backend-final.xml`). This is separate targeted
evidence, not a rewritten wrapper pass or a combined gate attestation.

The production frontend build and live-host browser setup/revocation check pass.
`data/phone-screen-fix/candidate.json` verifies the running backend identity and
private APK download. Source digest is
`6a11ca089c40f9532aeb7cec6587f53f358e217de64eef5a86499096a41eed03`.
Host restart retained actual phone credentials and task history.

The first emulator attempt failed on a cold read-only hierarchy observation before
capture setup. Its raw report remains under `emulator-01`. A bounded fresh hierarchy
read repaired the harness, and `emulator-02` passes the capture pilot. The final
candidate rerun adds the two-image recipe admission and uses the final audio-free
capture implementation. No failed report was overwritten.

Current APK SHA256:
`f82d4115987f9383174888cd6e9d4a0aa73baf4a0191b6cedcd0b97f0be443fc`.
Install the new APK over the existing app, preserving pairing, then reopen Jarvis
and retry the recipe with Chrome in front. Previously granted permissions need
not be granted again. Physical M11D confirmation remains pending; this is targeted
repair evidence, not a new combined fifteen-gate provider acceptance pass.
