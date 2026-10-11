# Recipe-from-Screen Reference Workflow

Status: completed for the accepted personal-use scope on 2026-10-11. The
[completion ledger](00-implementation-roadmap.md) owns status and accepted
limits; this document retains the phase contract and historical evidence.

## Completion

This phase is complete under the [final personal-use acceptance](reports/personal-use-accepted-2026-10-11.md).
The [reports index](reports/README.md) retains candidate-scoped verification,
failed observations and the accepted M8 caveats. Historical implementation
notes below do not assign new work or override the completion ledger.

## Outcome and scope

Turn an activation screenshot into one useful, source-backed recipe quickly. Demonstrate that a workflow can use shared context, research, clarification, continuation and publication, with a small registered result type. Stop when the requested result is adequately supported.

The recipe-specific implementation consists of a versioned definition, instructions, fixtures, a minimal `recipe.v1` schema and renderer, and catalog metadata. Shared capabilities own executable operations. No recipe-specific admission, scheduler, conversation store, research service or result lifecycle is required.

Shopping-list controls, serving calculators, dietary substitution machinery, equipment adaptation features, specialised extraction optimisations and polished recipe browsing are deferred. Ordinary model judgment may address a requested variation using shared tools, but these are not dedicated features or M4 acceptance requirements. Explicit user constraints must be respected or reported as unsupported.

## Contract

- Workflow ID/version: `recipe_from_screen@1`.
- Input: `screen-request.v2` with the user's request and authorised context references. Preserve explicit constraints in shared task context; do not invent recipe-specific preference inputs or represent assumptions as user requirements.
- Initial context: `screen_images`, a frozen invocation/latest collection with labels and provenance. Invocation is required for activation requests; latest is required for explicit current-screen requests. Expose an unavailable optional role. Confirm capture readiness before admission, allowing at most two additional seconds for pending capture. Upload distinct images concurrently after admission and start execution only after all promised uploads finish. Capture/upload errors retain the shared fail-on-error/no-automatic-retry contract.
- Output: the small `recipe.v1` result described below, published through the common result service.
- Soft objective: first validated publication within 30 seconds on the ready path. Measure the full path and separate queue, active execution and user-wait time.
- No overall task deadline or mandatory model-call count. Individual calls remain bounded. Investigate measured performance misses with bounded tuning and report remaining limitations.

## Initial execution guidance

Supply this procedure in reusable instructions:

1. Load the frozen invocation/latest collection through `task_context_images` in one call. Identify a visible dish or choose a suitable dish from the visible ingredients. A clear recipe request does not require confirmation of recipe versus ingredient-list intent. State additional ingredients as additions rather than claiming they were visible.
2. Ask a focused question only when ambiguity materially changes the requested outcome. Otherwise proceed with a reasonable interpretation and make uncertainty explicit.
3. Use native hosted web research to find adequate source material. Try alternatives when a source is inaccessible; reuse retained research when adequate.
4. Choose one adequate provider-backed recipe as the basis. Additional sources or research are needed only for a material evidence gap or user constraint; corroboration is not a mandatory round.
5. Produce concise ingredients and ordered instructions with source references. Check substantive completeness and consistency during the normal worker call.
6. Publish once the result is adequately supported. Concrete validation errors or evidence gaps can trigger targeted correction, clarification, more research or a classified failure through the shared runtime.

Do not add standalone planning, per-source summarising or production recipe-grading calls. The model handles interpretation and useful judgment; code validates schemas, source identity, permissions and publication preconditions. Successful schema validation does not prove culinary correctness.

## Minimal result type and presentation

Register `recipe.v1` in the common backend result-type and frontend renderer registries. Use the same payload for validation, persistence and rendering.

Required content:

- title;
- a nonempty list of ingredients with quantities expressed as readable text;
- a nonempty ordered list of instruction strings;
- source references resolving to completed native research events and usable source URLs.

Optional content is an uncertainty note, servings and timing expressed as readable text when supported. Do not require numeric quantities, ingredient IDs, step-to-ingredient graphs, scaling calculations, equipment lists or alternatives.

Shared provenance retains source URL/title, completed native event sequence and producing task/run/revision identity. It is provider-backed evidence, not a claim of an independently downloaded complete page. Reused evidence from an earlier revision remains attributable. Render clickable source links and a simple phone-friendly ingredient/step layout. Publish concise original wording rather than copying source prose wholesale.

Reject missing/empty required content, unknown native research references and unusable source URLs. Code validates completed same-task native events and reference integrity; substantive source adequacy remains worker judgment evaluated separately. Existing fencing, expected-base-revision and atomic-publication rules apply. Do not claim allergy safety or exact identification of hidden ingredients.

Before publication, show committed task progress or its pending question. Never display provisional ingredients/steps as a completed recipe. A slow task remains inspectable and cancellable through the common result view.

## Shared continuation

Completion releases the worker while preserving the task session, interpretation, constraints, evidence and result. A follow-up uses `continue_task` to create a run under the same task; an answer resumes the run that asked its question.

Use a simple clarification or wording/detail update to demonstrate continuation. Reuse retained evidence where adequate, fetch more only when the changed request needs it, and publish edits at the same URL with an expected base revision. Questions may finish without a revision. Failed or cancelled updates preserve the previous result. An ordinary follow-up needs no new screenshot.

## Shared capabilities and initial providers

These are Phase 4 capabilities consumed by both reference workflows, not recipe-owned services:

1. **Image/context:** use `task.context.read@1` and `task.context.images@2` to attach the labeled invocation/latest retained images to the substantive worker turn. Verify image support for the selected profile; never silently omit it or require a separate vision-model call.
2. **Research:** `research.native@1` enables native hosted Codex web research. The worker chooses queries, reads and alternate sources, retaining uncertainty when adequate material is unavailable. No separate discovery model, fixed discovery batches or local HTTP refetch is required.
3. **Provenance:** `task_history.native_research` exposes completed event references directly. Publish ordinary source URLs with `source_event_sequence` and declare the producing `source_event_sequences`. These are provider-backed references; they do not mechanically establish claim support or an independently downloaded complete page.
4. **Clarification/publication/finish:** use the common tools, durable questions and result service. Publication atomically commits the validated immutable revision and producing run's completion. Questions can complete through the common finish path without modifying a result.

Native research is enabled for this profile through research.native@1. Jarvis no longer runs a separate discovery model or raw HTTP fetch pipeline. task_history.native_research exposes completed search references directly. The worker cites ordinary URLs and source_event_sequence; typed publication checks same-task native provenance. External text remains untrusted evidence. Browser automation and paywall bypass remain outside this iteration.

## Compact acceptance set

Create a fixture manifest with stable IDs, permitted test assets, deterministic provider responses and expected outcomes. The reference cases are:

1. Clear image: useful source-backed completion through `recipe.v1`.
2. Material ambiguity: shared clarification followed by a correct resume.
3. Partial source failure: reuse adequate successful evidence; if all usable sources fail, do not publish an unsupported result.
4. Invalid output: return concrete validation errors and allow correction without exposing a completed invalid recipe.
5. Follow-up: reuse context and evidence, create a new run under the same task and revise the same URL.
6. Failed/cancelled update: keep the prior successful result readable.

Use an explicit user constraint in the clear or clarification fixture and assert compliance or a truthful unsupported outcome. Extensive dietary, regional, serving and equipment case matrices are not required.

Shared suites retain adversarial-content, capability denial, URL safety, malformed extraction, timeout/billing failure, recovery, stale-publication and cancellation coverage. Link those results from the recipe report instead of reproducing them in the workflow package. Existing capture/pool suites retain their respective platform assertions.

Run a small fixed set of representative real inputs with repeated ready-path and cold-start samples through agent-runnable tooling. Record completed/clarified/failed outcomes separately, evidence support, useful completeness, latency, sample counts and all model/tool usage. Assess source support and usefulness against retained evidence; label the assessment method and uncertainty rather than treating schema validity as semantic proof. Subjective human review is optional feedback and not an M4 or reference-workflow enablement dependency. Known unsupported material claims or ignored explicit constraints remain defects.

The 30-second goal remains soft. Report median/p95 and individual misses with sample counts; small samples do not establish a production latency guarantee. Bound optimisation to demonstrated shared-path bottlenecks rather than recipe feature expansion.

## Extensibility proof and platform boundary

After the shared platform and recipe example pass, add `research_screen@1`: a concise sourced explanation of the visible subject using the same screen input, image/context, research, clarification and publication capabilities, with `document.v1` output.

Adding that task must require only its definition, instructions, fixtures and enablement configuration. Changing transport, admission, routing prompts, scheduling, lifecycle or result service for it fails the extensibility check. Verify catalog discovery and routing for both examples.

M5 has verified the generic catalog, collection search/pagination, registered actions and namespaced app storage. Recipes uses the common collection infrastructure with its type, renderer and catalog metadata. The shared per-result saved flag demonstrates actions/storage; recipe edits remain ordinary task follow-ups. M10 revalidates this reuse. No specialised recipe collection behavior is required.

Keep candidate-scoped account/provider and personal-device evidence in the [completion ledger](00-implementation-roadmap.md). A useful reference task does not itself establish production rollout readiness.
## Completion evidence

See the [completion ledger](00-implementation-roadmap.md) and
[reports index](reports/README.md). Earlier progress reports are historical;
M0–M11 are closed under the user's accepted scope and caveats.
