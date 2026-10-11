# JARVIS M6–M10 feasibility review — 2026-10-04

The core product goals remain credible: durable tasks, general coding, screen workflows, background decisions, continuation and results. There is no evidence that the whole architecture needs replacing. However, completion under every unchanged constraint is not currently supported. The strict two-second admission gate is the main design risk; fresh-profile Windows execution is a real host/runtime blocker; account authorization remains an external dependency. Several acceptance defects also need correction before further expensive runs.

This is an assessment, not an approved roadmap revision or implementation-completion claim. Application code, credentials, account settings and device opt-ins were not changed.

## Evidence and scope

Reviewed the recent implementation thread, [roadmap](../00-implementation-roadmap.md), [progress report](m6-m10-progress.md), current routing/runtime/acceptance code and retained local diagnostics. Re-ran `test_task_acceptance.py`, `test_m6_m9_closure.py`, `test_android_audio_harness.py` and `test_live_client_delegation.py`: **35 passed in 48.80 seconds**. Small isolated probes additionally reproduced the candidate-hash and cutoff-validation defects below. No new paid provider run, emulator acceptance or account check was performed.

The prior aggregate's 567 backend passes, 191 frontend passes and 27 Android passes are historical evidence; twelve provider checks were skipped and later changes superseded that source. They establish substantial implemented functionality, not current M10 acceptance.

## 1. Admission latency: a real constraint conflict, not proof of impossibility

Both recorded repeated routes admitted 18/20 ready requests. Successful-sample p95 was **5.974 seconds managed** and **8.397 seconds client**, against **less than 2 seconds**. Failures remain failures and cannot be dropped to pass the corpus. The corrected one-sample diagnostic still took 5.721 seconds for activation and 6.222 seconds for fresh continuation. Its priority selector calls alone took 1.407–2.359 seconds. These figures come from the progress report and the corpus/performance diagnostics it links; they are not new measurements or population estimates.

The client path still waits for Live delegation, resolves a completed transcript, then awaits a nonstreaming Responses request before dispatching its tools (`openai_live_adapter.py:720`). Moving ownership of the selector to the client did not remove the second model call. The capture callback itself is much faster: repeated synchronous p95 was 0.595 ms; the latest single fresh callback took 72.330 ms. Capture micro-optimizations cannot plausibly recover several seconds by themselves.

[Official GPT-Live guidance](https://developers.openai.com/api/docs/guides/voice-agents#build-a-full-duplex-voice-agent) describes Live as the conversational frontend with delegated backend reasoning/tool use. A proposal to simply let the same Live model perform all admission tool selection is therefore not an established replacement supported by this review.

**Recommendation:** stop whole-corpus paid tuning until the product constraint is settled. Preserve the durable task architecture. Prefer separate targets for prompt, truthful conversational acknowledgment and durable admission, with the latter set from accepted product needs and measured reliability. This explicitly changes the current gate; an acknowledgment must never be counted as task acceptance. Do not invent a new admission threshold from these small failed runs.

If sub-two-second durable admission is non-negotiable, make routing feasibility its own bounded prerequisite before more M8 closure work. Measure speech-end → delegation/transcript-ready → selector start/complete → commit → client receipt. Explore a simpler supported critical path, including consuming a completed streamed tool call without waiting for unrelated output. Keep identity, complete-argument validation and durable commit intact. This is a hypothesis to test, not a promise that streaming will reach two seconds. If the provider stages already consume the budget, the selected voice/routing architecture or target must change. Do not add speculative tasks, keyword routing or a second competing dispatcher to manufacture a pass.

## 2. Windows sandbox: host/runtime blocker with a plausible repair path

`data/closure-m6-live.json` records a declared inside-root write denied by Codex 0.155.1's unelevated sandbox before coding began. The historical M0 scratch-root pass had different ACL circumstances, so it does not establish fresh-profile provisioning. Both the worker adapter and project command broker explicitly select unelevated mode. This is not evidence that coding, durable commands or continuation are fundamentally infeasible.

**Recommendation:** first reproduce the three filesystem probes without model work in a clean, project-owned root/profile; inspect the sandbox setup/log and root inheritance. Distinguish a pytest temporary-directory ACL issue, missing supported initialization and an actual runtime defect. Correct scoped provisioning through supported mechanisms and repeat inside-write, read-only denial, outside-write denial and descendant-stop/lease-transfer checks. Only then rerun failing-test → repair → passing-test → restart/same-task continuation.

If 0.155.1 cannot provide that boundary, propose a deliberately revalidated runtime/sandbox change. [Current Windows documentation](https://learn.chatgpt.com/docs/windows/windows-sandbox) prefers elevated sandbox setup and treats unelevated as a fallback; it also describes WSL as an alternative when native modes are unsuitable. Current documentation does not prove a fix for the pinned build. Elevated setup changes host configuration and needs appropriate user direction; keep any upgrade isolated until the original containment contracts pass. Broad ACL grants and unrestricted execution are not repairs.

## 3. Capture performance: fix the test definition, then address real overhead

The plan requires no visible jank introduced by capture. `performance_proof()` in `android/scripts/verify-audio-corpus.py:388` instead sets `overlay_jank_observed=any(jank)` over disabled baseline, activation and fresh samples. A single baseline missed frame therefore fails capture even if enabling it adds none. The report's SwiftShader environment is a potential confounder, not proof that all jank is harmless.

There is a second discrepancy: the plan limits the **increase in Live-ready p95** to 150 ms. The harness pairs separately collected runs by index and takes **p95 of their differences**. Those are different statistics. Recalculation from the retained 20+20 startup observations gives a 954.074 ms difference of p95s versus 1738.500 ms p95 of index-paired differences. **Both miss 150 ms**; correcting the statistic will not turn that run into a pass. The later single sample's negative overhead cannot establish a regression pass either.

**Recommendation:** define a controlled, interleaved baseline/enabled comparison with equal observation windows, frame counts/rates and frame-duration distributions. Predeclare a meaningful capture-induced regression tolerance; retain zero ANR/underrun requirements and all missing/failed observations. Measure the planned difference of p95s and keep per-trial deltas as diagnostics. Use a stable supported graphics environment and separate provider connection variability from local startup work. Complete twenty fresh observations and the full spoken corpus. Preserve M11's physical-device deferral; emulator results cannot prove physical smoothness.

## 4. Billing and subscription: external setup plus fixable implementation defects

There is a documented route to the budget requirement. OpenAI's [project spend-limit API](https://developers.openai.com/api/reference/resources/admin/subresources/organization/subresources/projects/subresources/spend_limit/methods/retrieve) exposes a hard monthly limit and `enforcement.status`; an [organization equivalent](https://developers.openai.com/api/reference/resources/admin/subresources/organization/subresources/spend_limit/methods/retrieve) exists too. This is stronger evidence than assuming an alert threshold or prepaid balance is sufficient. Availability and configuration for this particular account remain unverified. Prefer the narrow Jarvis project scope where suitable; organization changes could affect unrelated applications.

The current implementation has two problems:

- `validate_observation("provider_cutoff", ...)` only checks six fields are truthy (`acceptance.py:173`). An isolated probe passed an unrelated account, `invalid_api_key`, an unfetched URI and `not-a-hash`. The validator did not authenticate the source, check account binding, validate the digest or require a billing-specific rejection. This does not itself create charges, but it cannot substantiate the safety claim.
- `provider_cutoff_verified()` imports the acceptance loader and hashes the candidate on each background decision. The loader expires evidence after 24 hours and demands exact source/config equality. With cutoff enforcement enabled, a documentation edit or elapsed day can block background decisions even though provider billing configuration is unchanged. The check runs before selecting subscription versus API execution.

**Recommendation:** separate provider account-policy verification, executable denial-handling tests and release acceptance. Obtain a trusted, account/project-bound hard-limit observation with its own refresh/invalidation policy; bind it to the credentials' scope without exposing secrets. Use controlled fixtures to prove terminal billing denial stops retries across API paths. Keep candidate-bound integration acceptance, but do not require a new real exhaustion event for every build or every day. The existing mandatory `rejection_request_id` contract should be reconciled explicitly with the roadmap's instruction to simulate exhaustion rather than deliberately exhaust funds. Do not replace the provider boundary with a local budget engine or a self-authored “verified” file.

Separately, a supported login to the dedicated Jarvis subscription profile is still necessary. Then prove actual subscription execution, ownership competition, restart and safe API handoff. A user's desktop subscription session is not that evidence. These account steps require user participation; no code-only change can supply authorization.

## 5. Candidate evidence: improve validity and reduce needless reruns

An isolated probe against `candidate_identity()` confirmed that changing `backend/workflows/.../instructions.md` does **not** change the source digest, while changing `docs/jarvis-tasks/...md` does. The function omits the workflow tree but includes roadmap prose. Workflow allowlist digests provide separate admission protection, but they do not make the reported source identity comprehensive.

Phase 7 also allows gate-specific configurations within one candidate manifest, while the implementation requires exact equality of a flat configuration digest. This helps explain why isolated emulator diagnostics cannot be imported into the aggregate. Simply removing equality checks would lose the valuable backend/source/database attestation.

**Recommendation:** hash executable workflow definitions/instructions and the relevant test/build/runtime assets; version the specification separately. Use a frozen source/runtime manifest with explicitly declared gate configuration profiles. Match each observation to its expected profile and backend instance/database; reject undeclared differences. Freeze source before final acceptance and avoid re-running the entire suite after report-only edits. Wire capture-performance collection into the composed run or make its required evidence-generation step explicit: the current composed audio invocation does not pass `--capture-performance`.

## Recommended order and completion judgment

1. Repair acceptance identity, cutoff validation/lifetime separation and performance metrics, with adversarial regression tests for the reproduced defects.
2. Prove clean-root sandbox provisioning without paid inference; then close the actual coding/restart gate.
3. Decide the latency contract. Keep the product architecture and adopt an explicit admission target, or make a materially different routing path prove the strict target before proceeding.
4. Provision/verify the separate subscription profile and provider hard limit through authorized account setup; run real continuation/fallback and simulated denial tests.
5. Finish corpus reliability, multi-intent handling, repeated fresh capture and startup/frame performance. Then run one frozen-candidate M10 acceptance with declared test profiles and no missing required assertions.

M6 and M9 have concrete investigation/setup paths. M7's implemented contracts have substantial deterministic coverage but still need their required provider evidence. M8 remains the uncertain milestone because latency is several times the threshold and the full corpus/performance gate is incomplete. M10 is achievable only after those conditions are actually resolved or the user explicitly revises the requirement. M11 remains deferred as agreed. There is no honest basis to label the unchanged plan complete, and no demonstrated reason to abandon JARVIS's durable task design.
