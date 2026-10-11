"use client";

import Link from "@/components/CockpitLink";
import { use, useCallback, useEffect, useMemo, useState } from "react";
import { ArrowLeft, Ban, Bot, ExternalLink, Loader2, RefreshCw, Send } from "lucide-react";

import { ChatMessage } from "@/components/ChatMessage";
import { TaskToolOverrides } from "@/components/TaskToolOverrides";
import type { ToolOverrides, TaskResultIntent } from "@/lib/api";
import { acknowledgePushDelivery, answerTaskQuestion, continueTask, controlTask, getTask, type TaskArtifact, type TaskDetail } from "@/lib/api";
import { taskEventToChatMessage } from "@/lib/task-trace";
import { ACTIVE_TASK_STATUSES, taskStatusLabel } from "@/lib/task-status";

const RETRYABLE = new Set(["failed", "interrupted", "cancelled"]);

function textReport(artifact: TaskArtifact): { title: string; body: string; sources: number[] } | null {
  const envelope = artifact.content_json;
  if (artifact.artifact_type !== "workflow_result" || envelope?.schema !== "text-report.v1") return null;
  const content = envelope.content;
  if (!content || typeof content !== "object" || Array.isArray(content)) return null;
  const value = content as Record<string, unknown>;
  if (typeof value.title !== "string" || typeof value.body !== "string" || !Array.isArray(value.source_event_sequences)) return null;
  const sources = value.source_event_sequences.filter((item): item is number => typeof item === "number" && Number.isInteger(item) && item > 0);
  return { title: value.title, body: value.body, sources };
}

export default function TaskDetailPage({ params }: { params: Promise<{ taskId: string }> }) {
  const { taskId } = use(params);
  const [task, setTask] = useState<TaskDetail | null>(null);
  const [answers, setAnswers] = useState<Record<string, string>>({});
  const [busy, setBusy] = useState(false);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [linkedQuestionId, setLinkedQuestionId] = useState<string | null>(null);
  const [followup, setFollowup] = useState("");
  const [toolOverrides, setToolOverrides] = useState<ToolOverrides>();
  const [resultMode, setResultMode] = useState<"answer" | "update" | "new">("answer");
  const [selectedResultId, setSelectedResultId] = useState("");
  const [immediateStop, setImmediateStop] = useState(false);

  useEffect(() => {
    const query = new URLSearchParams(window.location.search);
    setLinkedQuestionId(query.get("question"));
    const deliveryId = query.get("delivery");
    if (deliveryId) void acknowledgePushDelivery(deliveryId, "opened");
  }, []);

  useEffect(() => {
    if (!task || !linkedQuestionId) return;
    document.getElementById(`question-${linkedQuestionId}`)?.scrollIntoView({
      behavior: "smooth",
      block: "center",
    });
  }, [linkedQuestionId, task]);

  const refresh = useCallback(async () => {
    try {
      setTask(await getTask(taskId));
      setError(null);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "Unable to load task");
    } finally {
      setLoading(false);
    }
  }, [taskId]);

  useEffect(() => {
    void refresh();
    const timer = window.setInterval(() => void refresh(), 3000);
    return () => window.clearInterval(timer);
  }, [refresh]);

  const trace = useMemo(() => task?.events
    .filter((event) => event.event_type !== "agent.native.event")
    .map(taskEventToChatMessage) ?? [], [task]);
  const currentRun = task?.runs.find(run => run.id === task.current_run_id);
  const supportsAnswer = currentRun?.execution_kind !== "workflow"
    || Boolean(currentRun.configuration?.workflow?.capabilities?.includes("task.finish@1"));
  const effectiveResultMode = resultMode === "answer" && !supportsAnswer ? "update" : resultMode;

  const act = async (action: string, parameters: Record<string, unknown> = {}) => {
    if (busy || !task) return;
    let reason: string | undefined;
    if (
      action === "retry"
      && task.attempts.at(-1)?.terminal_reason === "resource_changed"
      && !task.terminal_summary?.includes("cannot be checked")
    ) {
      reason = window.prompt(
        "A shared directory changed while this task was paused. Review its current state, then explain why retrying is safe:",
      )?.trim();
      if (!reason) return;
      parameters = { ...parameters, accept_resource_changes: true };
    }
    if (["cancel", "restart_from_beginning"].includes(action) && !window.confirm(
      action === "cancel" ? "Cancel this task?" : "Restart from the beginning in a new workspace generation?",
    )) return;
    setBusy(true);
    setError(null);
    try {
      await controlTask(taskId, action, parameters, task.current_run_id, task.control_revision, reason);
      await refresh();
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : `Unable to ${action} task`);
    } finally {
      setBusy(false);
    }
  };

  const submitFollowup = async () => {
    const content = followup.trim();
    if (!content || busy) return;
    setBusy(true);
    setError(null);
    try {
      const selected = task?.results?.find(result => result.public_id === selectedResultId) || task?.results?.[0];
        const result: TaskResultIntent | undefined = selected ? (effectiveResultMode === "new" ? { mode: "new" } : effectiveResultMode === "answer" ?
            { mode: "answer", public_id: selected.public_id } :
            { mode: "update", public_id: selected.public_id, expected_revision: selected.revision_number }) : undefined;
        if (toolOverrides) await continueTask(taskId, content, result, { tools: toolOverrides });
        else if (result) await continueTask(taskId, content, result);
        else await continueTask(taskId, content);
        setFollowup("");
        setToolOverrides(undefined);
      await refresh();
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "Unable to continue task");
    } finally {
      setBusy(false);
    }
  };

  const answer = async (questionId: string, decision?: "approve" | "deny") => {
    const content = decision ?? (answers[questionId] || "").trim();
    if (!content || busy) return;
    setBusy(true);
    try {
      await answerTaskQuestion(taskId, questionId, content);
      setAnswers((current) => ({ ...current, [questionId]: "" }));
      await refresh();
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "Unable to answer question");
    } finally {
      setBusy(false);
    }
  };

  if (loading) return <div className="flex h-full items-center justify-center text-zinc-500"><Loader2 className="animate-spin" /></div>;

  return (
    <div className="flex h-full min-h-0 flex-col bg-zinc-950">
      <header className="border-b border-zinc-800 bg-zinc-900 px-4 py-3 md:px-6">
        <div className="mx-auto flex max-w-6xl items-center gap-3">
          <Link href="/tasks" aria-label="Back to tasks" className="rounded-lg p-2 text-zinc-400 hover:bg-zinc-800 hover:text-white"><ArrowLeft size={17} /></Link>
          <Bot size={18} className="text-blue-400" />
          <div className="min-w-0 flex-1">
            <h1 className="truncate text-sm font-semibold text-zinc-100">{task?.goal || "Task"}</h1>
            <p className="mt-0.5 text-xs text-zinc-500">{task?.id}</p>
          </div>
          {task && task.status === "running" && <>
            <button onClick={() => void act("yield", { immediate: immediateStop })} disabled={busy || Boolean(task.pending_control_id)} className="rounded-xl border border-zinc-700 px-3 py-2 text-xs text-zinc-200 disabled:opacity-40">Yield</button>
            <button onClick={() => void act("suspend", { immediate: immediateStop })} disabled={busy || Boolean(task.pending_control_id)} className="rounded-xl border border-zinc-700 px-3 py-2 text-xs text-zinc-200 disabled:opacity-40">Suspend</button>
          </>}
          {task && task.status === "paused" && <button onClick={() => void act("resume")} disabled={busy} className="rounded-xl bg-blue-600 px-3 py-2 text-xs text-white disabled:opacity-40">Resume</button>}
          {task && ACTIVE_TASK_STATUSES.has(task.status) && (
            <button onClick={() => void act("cancel", { immediate: true })} disabled={busy || Boolean(task.pending_control_id)} className="flex items-center gap-1.5 rounded-xl border border-red-500/30 px-3 py-2 text-xs text-red-300 disabled:opacity-40"><Ban size={13} /> Cancel</button>
          )}
          {task && RETRYABLE.has(task.status) && (
            <button onClick={() => void act("retry")} disabled={busy} className="flex items-center gap-1.5 rounded-xl bg-blue-600 px-3 py-2 text-xs text-white disabled:opacity-40"><RefreshCw size={13} /> Retry</button>
          )}
        </div>
      </header>

      <main className="min-h-0 flex-1 overflow-y-auto p-4 md:p-6">
        <div className="mx-auto grid max-w-6xl grid-cols-1 gap-5 lg:grid-cols-[minmax(0,1fr)_19rem]">
          <section className="min-w-0 space-y-3">
            <section className="rounded-2xl border border-zinc-800 bg-zinc-900 p-4">
              <h2 className="text-sm font-medium text-zinc-100">Continue this task</h2>
              <p className="mt-1 text-xs text-zinc-500">Follow-ups keep the same task history and workspace.</p>
              {Boolean(task?.results?.length) && <div className="mt-3 flex flex-wrap gap-2 text-sm">
                <select aria-label="Follow-up result intent" value={effectiveResultMode} onChange={event => setResultMode(event.target.value as typeof resultMode)} className="rounded-lg border border-zinc-700 bg-zinc-950 p-2">
                  {supportsAnswer && <option value="answer">Answer only — keep results unchanged</option>}
                  <option value="update">Edit selected result</option>
                  <option value="new">Create additional result</option>
                </select>
                {effectiveResultMode !== "new" && <select aria-label="Selected result" value={selectedResultId || task?.results?.[0]?.public_id} onChange={event => setSelectedResultId(event.target.value)} className="min-w-0 rounded-lg border border-zinc-700 bg-zinc-950 p-2">
                  {task?.results?.map(result => <option key={result.public_id} value={result.public_id}>{result.title}</option>)}
                </select>}
              </div>}
              <div className="mt-3 flex gap-2">
                <textarea value={followup} onChange={(event) => setFollowup(event.target.value)} rows={2} className="min-w-0 flex-1 resize-y rounded-xl border border-zinc-700 bg-zinc-950 px-3 py-2 text-sm text-zinc-100" placeholder="Add a follow-up…" />
                <button onClick={() => void submitFollowup()} disabled={busy || !followup.trim()} className="rounded-xl bg-blue-600 px-4 text-sm text-white disabled:opacity-40">Send</button>
              </div>
              <TaskToolOverrides value={toolOverrides} onChange={setToolOverrides} />
            </section>
            {Boolean(task?.results?.length) && <section className="rounded-2xl border border-zinc-800 bg-zinc-900 p-4">
              <h2 className="text-sm font-medium">Maintained results</h2>
              {task?.results?.map(result => <Link key={result.public_id} href={result.url} className="mt-2 block text-sm text-sky-400">{result.title} · {result.status}</Link>)}
            </section>}
            <h2 className="text-xs font-semibold uppercase tracking-[0.16em] text-zinc-500">Agent trace</h2>
            {trace.map((message) => <ChatMessage key={message.id} message={message} />)}
            {trace.length === 0 && <p className="rounded-2xl border border-zinc-800 bg-zinc-900 p-4 text-sm text-zinc-500">No events yet.</p>}
            {task?.artifacts.filter((artifact) => artifact.artifact_type !== "task.context.summary").map((artifact) => {
              const report = textReport(artifact);
              return (
                <article key={artifact.id} className="rounded-2xl border border-emerald-500/20 bg-emerald-500/5 p-4">
                  <div className="text-xs font-medium uppercase tracking-wider text-emerald-300">{report ? "Workflow result" : artifact.artifact_type}</div>
                  <h3 className="mt-1 text-sm font-medium text-zinc-100">{report?.title || artifact.title || artifact.mime_type}</h3>
                  {report && <p className="mt-3 whitespace-pre-wrap text-sm text-zinc-300">{report.body}</p>}
                  {report && report.sources.length > 0 && <p className="mt-3 text-xs text-zinc-500">Task evidence: {report.sources.map((source) => `#${source}`).join(", ")}</p>}
                  {!report && artifact.content_text && <pre className="mt-3 whitespace-pre-wrap font-sans text-sm text-zinc-300">{artifact.content_text}</pre>}
                  {artifact.uri && <a href={artifact.uri} className="mt-2 block break-all text-xs text-blue-400">{artifact.uri}</a>}
                </article>
              );
            })}
            {error && <div className="rounded-xl border border-red-500/30 bg-red-500/10 px-4 py-3 text-sm text-red-200">{error}</div>}
          </section>

          <aside className="min-w-0 space-y-4">
            {task && (
              <section className="rounded-2xl border border-zinc-800 bg-zinc-900 p-4 text-sm">
                <h2 className="font-medium text-zinc-100">State</h2>
                <dl className="mt-3 space-y-2 text-xs">
                  <div className="flex justify-between gap-3"><dt className="text-zinc-500">Status</dt><dd className="capitalize text-zinc-200">{taskStatusLabel(task)}</dd></div>
                  <div className="flex justify-between gap-3"><dt className="text-zinc-500">Queue</dt><dd className="text-zinc-200">{task.queue_position ? `#${task.queue_position}` : "—"}</dd></div>
                  <div className="flex justify-between gap-3"><dt className="text-zinc-500">Worker</dt><dd className={task.worker_health.status === "online" ? "text-emerald-300" : "text-zinc-400"}>{task.worker_health.status}</dd></div>
                  <div className="flex justify-between gap-3"><dt className="text-zinc-500">Origin</dt><dd className="text-zinc-200">{task.origin_surface.replaceAll("_", " ")}</dd></div>
                  <div className="flex justify-between gap-3"><dt className="text-zinc-500">Importance</dt><dd><select value={task.importance} disabled={busy} onChange={(event) => void act("set_importance", { importance: event.target.value })} className="rounded bg-zinc-950 text-zinc-200"><option value="low">Low</option><option value="normal">Normal</option><option value="high">High</option></select></dd></div>
                </dl>
                {task.status === "running" && <label className="mt-3 flex items-center gap-2 text-xs text-zinc-400"><input type="checkbox" checked={immediateStop} onChange={(event) => setImmediateStop(event.target.checked)} />Stop immediately for yield or suspend</label>}
                {(task.status === "running" || RETRYABLE.has(task.status)) && <button onClick={() => void act("restart_from_beginning", { immediate: true })} disabled={busy || Boolean(task.pending_control_id)} className="mt-3 flex items-center gap-1.5 text-xs text-amber-300 disabled:opacity-40"><RefreshCw size={13} /> Restart from beginning</button>}
                {task.origin_conversation_id && (
                  <Link
                    href={`/chat?conversation=${task.origin_conversation_id}`}
                    className="mt-3 inline-flex items-center gap-1.5 text-xs text-blue-400 hover:text-blue-300"
                  >
                    Open related conversation <ExternalLink size={12} />
                  </Link>
                )}
                {task.cancellation_requested_at && task.status === "running" && <p className="mt-3 rounded-lg bg-amber-500/10 p-2 text-xs text-amber-200">Cancellation requested; waiting for the worker to exit safely.</p>}
                {task.status_reason && <p className="mt-3 rounded-lg bg-amber-500/10 p-2 text-xs text-amber-200">{task.status_reason}</p>}
                {task.terminal_summary && <p className="mt-3 border-t border-zinc-800 pt-3 text-zinc-300">{task.terminal_summary}</p>}
              </section>
            )}

            {task?.questions.filter((question) => question.state === "asked").map((question) => (
              <section
                key={question.id}
                id={`question-${question.id}`}
                className={linkedQuestionId === question.id
                  ? "rounded-2xl border border-amber-300 bg-amber-500/10 p-4 ring-2 ring-amber-300/30"
                  : "rounded-2xl border border-amber-500/30 bg-amber-500/5 p-4"}
              >
                <h2 className="text-xs font-medium uppercase tracking-wider text-amber-300">{question.permission ? "Permission required" : "Input required"}</h2>
                <p className="mt-2 text-sm text-zinc-200">{question.question}</p>
                {question.permission && <pre className="mt-3 max-h-72 overflow-auto whitespace-pre-wrap rounded-lg bg-zinc-950 p-3 text-xs text-zinc-300">{JSON.stringify(question.permission.arguments, null, 2)}</pre>}
                <div className="mt-3 flex gap-2">
                  {question.permission ? <>
                    <button onClick={() => void answer(question.id, "approve")} disabled={busy} className="rounded-lg bg-amber-500 px-4 py-2 text-zinc-950 disabled:opacity-40">Approve this action</button>
                    <button onClick={() => void answer(question.id, "deny")} disabled={busy} className="rounded-lg border border-zinc-600 px-4 py-2 disabled:opacity-40">Deny</button>
                  </> : <>
                  <input value={answers[question.id] || ""} onChange={(event) => setAnswers((current) => ({ ...current, [question.id]: event.target.value }))} className="min-w-0 flex-1 rounded-lg border border-zinc-700 bg-zinc-950 px-3 py-2 text-sm text-zinc-100 outline-none focus:border-amber-500" />
                  <button aria-label="Answer task question" onClick={() => void answer(question.id)} disabled={busy || !(answers[question.id] || "").trim()} className="rounded-lg bg-amber-500 px-3 text-zinc-950 disabled:opacity-40"><Send size={14} /></button>
                  </>}
                </div>
              </section>
            ))}

            <section className="rounded-2xl border border-zinc-800 bg-zinc-900 p-4">
              <h2 className="text-sm font-medium text-zinc-100">Attempts</h2>
              <div className="mt-3 space-y-2">
                {task?.attempts.map((attempt) => (
                  <div key={attempt.id} className="rounded-xl border border-zinc-800 bg-zinc-950 p-3 text-xs">
                    <div className="flex justify-between"><span className="font-medium text-zinc-200">Attempt {attempt.attempt_number}</span><span className="text-zinc-500">{attempt.terminal_reason || (attempt.started_at ? "running" : "queued")}</span></div>
                    {attempt.agent_runtime_reference && <div className="mt-1 truncate text-zinc-600">runtime {attempt.agent_runtime_reference}</div>}
                  </div>
                ))}
              </div>
            </section>

            <section className="rounded-2xl border border-zinc-800 bg-zinc-900 p-4 text-xs">
              <h2 className="text-sm font-medium text-zinc-100">Turns and runs</h2>
              <div className="mt-3 space-y-2">{task?.runs.map((run) => <div key={run.id} className="rounded-lg bg-zinc-950 p-2 text-zinc-300">Run {run.ordinal} · {run.status}{run.terminal_summary && <p className="mt-1 text-zinc-500">{run.terminal_summary}</p>}</div>)}</div>
              <div className="mt-3 space-y-2">{task?.turns.map((turn) => <div key={turn.id} className="border-t border-zinc-800 pt-2 text-zinc-400"><span className="capitalize text-zinc-500">{turn.role}</span> · {turn.content}</div>)}</div>
              {Boolean(task?.operations.length) && <div className="mt-3 border-t border-zinc-800 pt-3 text-zinc-400">Operations: {task?.operations.map((operation) => `${operation.operation_type} (${operation.state})`).join(", ")}</div>}
              {Boolean(task?.controls.length) && <div className="mt-2 text-zinc-500">Controls: {task?.controls.map((control) => `${control.action} (${control.state})`).join(", ")}</div>}
            </section>

            {Boolean(task?.targets.length) && (
              <section className="rounded-2xl border border-zinc-800 bg-zinc-900 p-4">
                <h2 className="text-sm font-medium text-zinc-100">Targets</h2>
                <div className="mt-2 space-y-2 break-all text-xs text-zinc-400">{task?.targets.map((target) => <div key={target.id}><span className="text-zinc-500">{target.resource_type}</span> · {target.resource_key} {target.mutable ? "(mutable)" : ""}</div>)}</div>
              </section>
            )}
          </aside>
        </div>
      </main>
    </div>
  );
}
