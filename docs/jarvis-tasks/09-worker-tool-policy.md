# Worker tool architecture — 2026-10-08

This revision supersedes the former all-broker tool profile. GPT-Live's managed
admission route, durable tasks, scheduling, result platform and dedicated
subscription ownership remain the same.

## Tool choices and authority

The worker uses Codex's hosted web research. Jarvis no longer runs a separate
research model or makes a local HTTP download a prerequisite for publication.
An inaccessible source is a tool observation the worker can recover from by
trying another source. Completed native web-search events and normal source
URLs are retained with results. This is provider-backed provenance; it is not
proof of an independently downloaded complete page or mechanical proof that
every claim is supported. The worker remains responsible for assessing evidence.

Global Settings controls four groups: local coding, research, subagents and
approved skills. Workflow manifests and individual task requests can add/remove
groups. Task additions can restore a workflow's clutter reduction; global
disablement always wins. Tool selection is separate from authority. Before its
first execution, each run freezes its resolved policy, revision, command mode
and approved skills. Resumed execution reuses that selection. A queued run that
has not started resolves the current defaults when it starts. Revocation pauses
affected active turns; grants never expand an in-flight turn's permissions.

The Jarvis MCP bridge retains history, frozen plural images, durable questions,
atomic typed publication and finish. The private file aliases and custom research
pipeline are removed. Actual authentication, billing and quota errors remain;
there is no spending-cutoff proof or duplicate budget system.

Native subagents share the parent task and its host containment. Their native
thread/item references are retained, including recursively discovered children.
They receive no account configuration surface or additional permission grant.
Skills are disabled unless an owner approves their absolute SKILL.md path.
Implicit plugin/app access, hooks and skill dependency installation are disabled.

## Windows execution decision

Credential-free unelevated native checks passed read-only denial, allowed-root
writes and outside-root denial across two task folders and two host starts.
Real Python tempfile creation still fails: its directory ACL prevents the
restricted token from writing/cleaning up. A basic wrapper pass is insufficient.
Native local execution therefore remains gated. Selecting native mode does not
retry a rejected command with broader access.

The existing explicit `TASK_COMMAND_MODE=trusted` choice retains the command
broker and project file tools for coding. Commands inherit the Windows user's
filesystem and network access. Brokered file edits are constrained to owned,
configured roots. Commands retain durable started/results/uncertain records,
directory leases, stale-run fencing and Job Object descendant termination. This
is an operational fallback, not native filesystem confinement. Native research,
approved skills and subagents can be used independently of native local coding.

Switching to native coding requires both exact-boundary checks and representative
Python/Node temporary files, subprocesses, nonzero test exits, cleanup, restart
and descendant-stop acceptance. No custom sandbox infrastructure, third-party
patch, repeated ACL repair or VM/container is introduced.

## Connector framework

Supported integrations register stable connector/tool IDs in code. Per-account,
per-tool permissions are `allow`, `ask_first` or `disabled`; unconfigured and
unregistered tools are disabled. Workers cannot change these settings.
Ask-first requests persist the exact account/tool/arguments and policy revision
as a human-only task question. Only an explicit approve/deny answer is accepted.
Changing arguments or permissions requires a new decision. Approved effects
still use the uncertain-operation journal: an unknown send cannot be retried
with another operation key. Successful receipts can be replayed without sending
again. Revocation prevents subsequent invocations; it cannot undo an external
effect already sent.

The framework is implemented and mock-tested. Real connectors and browser
automation remain deferred; no new account is connected or external send enabled.

## Acceptance and scope

Candidate identity includes runtime settings, executable workflow definitions,
persisted tool permissions and approved skill contents. Documentation-only
reports remain excluded. M8's four explicit accepted caveats stay unchanged;
the final ready-admission check remains one sample below 4.5 seconds. M11 is complete under the [personal-use acceptance](reports/personal-use-accepted-2026-10-11.md). M10 passed on 2026-10-09 at frozen source c8a0aec7: fifteen required
assertions passed and the four accepted M8 exceptions remain explicit. Final
admission was 3689.1404 ms. See the [completion report](reports/worker-tools-closure-2026-10-08.md).
Native coding's workload failure is recorded separately from acceptance of
explicit trusted mode. Normal personal use is accepted as of 2026-10-11.
