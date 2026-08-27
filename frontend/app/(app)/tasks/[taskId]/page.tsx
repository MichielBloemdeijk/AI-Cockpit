"use client";

import Link from "next/link";
import { use, useCallback, useEffect, useMemo, useState } from "react";
import { ArrowLeft, Ban, Bot, Loader2, RefreshCw, Send } from "lucide-react";

import { ChatMessage } from "@/components/ChatMessage";
import { answerTaskQuestion, cancelTask, getTask, retryTask, type TaskDetail } from "@/lib/api";
import { taskEventToChatMessage } from "@/lib/task-trace";

const RETRYABLE = new Set(["failed", "interrupted", "cancelled"]);
const ACTIVE = new Set(["queued", "running", "waiting_for_input"]);

export default function TaskDetailPage({ params }: { params: Promise<{ taskId: string }> }) {
  const { taskId } = use(params);
  const [task, setTask] = useState<TaskDetail | null>(null);
  const [answers, setAnswers] = useState<Record<string, string>>({});
  const [busy, setBusy] = useState(false);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

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

  const trace = useMemo(() => task?.events.map(taskEventToChatMessage) ?? [], [task]);

  const act = async (action: "cancel" | "retry") => {
    if (busy) return;
    setBusy(true);
    setError(null);
    try {
      await (action === "cancel" ? cancelTask(taskId) : retryTask(taskId));
      await refresh();
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : `Unable to ${action} task`);
    } finally {
      setBusy(false);
    }
  };

  const answer = async (questionId: string) => {
    const content = (answers[questionId] || "").trim();
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
          {task && ACTIVE.has(task.status) && (
            <button onClick={() => void act("cancel")} disabled={busy || Boolean(task.cancellation_requested_at)} className="flex items-center gap-1.5 rounded-xl border border-red-500/30 px-3 py-2 text-xs text-red-300 disabled:opacity-40"><Ban size={13} /> Cancel</button>
          )}
          {task && RETRYABLE.has(task.status) && (
            <button onClick={() => void act("retry")} disabled={busy} className="flex items-center gap-1.5 rounded-xl bg-blue-600 px-3 py-2 text-xs text-white disabled:opacity-40"><RefreshCw size={13} /> Retry</button>
          )}
        </div>
      </header>

      <main className="min-h-0 flex-1 overflow-y-auto p-4 md:p-6">
        <div className="mx-auto grid max-w-6xl gap-5 lg:grid-cols-[minmax(0,1fr)_19rem]">
          <section className="min-w-0 space-y-3">
            <h2 className="text-xs font-semibold uppercase tracking-[0.16em] text-zinc-500">Agent trace</h2>
            {trace.map((message) => <ChatMessage key={message.id} message={message} />)}
            {trace.length === 0 && <p className="rounded-2xl border border-zinc-800 bg-zinc-900 p-4 text-sm text-zinc-500">No events yet.</p>}
            {task?.artifacts.map((artifact) => (
              <article key={artifact.id} className="rounded-2xl border border-emerald-500/20 bg-emerald-500/5 p-4">
                <div className="text-xs font-medium uppercase tracking-wider text-emerald-300">{artifact.artifact_type}</div>
                <h3 className="mt-1 text-sm font-medium text-zinc-100">{artifact.title || artifact.mime_type}</h3>
                {artifact.content_text && <pre className="mt-3 whitespace-pre-wrap font-sans text-sm text-zinc-300">{artifact.content_text}</pre>}
                {artifact.uri && <a href={artifact.uri} className="mt-2 block break-all text-xs text-blue-400">{artifact.uri}</a>}
              </article>
            ))}
            {error && <div className="rounded-xl border border-red-500/30 bg-red-500/10 px-4 py-3 text-sm text-red-200">{error}</div>}
          </section>

          <aside className="space-y-4">
            {task && (
              <section className="rounded-2xl border border-zinc-800 bg-zinc-900 p-4 text-sm">
                <h2 className="font-medium text-zinc-100">State</h2>
                <dl className="mt-3 space-y-2 text-xs">
                  <div className="flex justify-between gap-3"><dt className="text-zinc-500">Status</dt><dd className="capitalize text-zinc-200">{task.status.replaceAll("_", " ")}</dd></div>
                  <div className="flex justify-between gap-3"><dt className="text-zinc-500">Queue</dt><dd className="text-zinc-200">{task.queue_position ? `#${task.queue_position}` : "—"}</dd></div>
                  <div className="flex justify-between gap-3"><dt className="text-zinc-500">Worker</dt><dd className={task.worker_health.status === "online" ? "text-emerald-300" : "text-zinc-400"}>{task.worker_health.status}</dd></div>
                  <div className="flex justify-between gap-3"><dt className="text-zinc-500">Origin</dt><dd className="text-zinc-200">{task.origin_surface.replaceAll("_", " ")}</dd></div>
                </dl>
                {task.cancellation_requested_at && task.status === "running" && <p className="mt-3 rounded-lg bg-amber-500/10 p-2 text-xs text-amber-200">Cancellation requested; waiting for the worker to exit safely.</p>}
                {task.terminal_summary && <p className="mt-3 border-t border-zinc-800 pt-3 text-zinc-300">{task.terminal_summary}</p>}
              </section>
            )}

            {task?.questions.filter((question) => question.state === "asked").map((question) => (
              <section key={question.id} className="rounded-2xl border border-amber-500/30 bg-amber-500/5 p-4">
                <h2 className="text-xs font-medium uppercase tracking-wider text-amber-300">Input required</h2>
                <p className="mt-2 text-sm text-zinc-200">{question.question}</p>
                <div className="mt-3 flex gap-2">
                  <input value={answers[question.id] || ""} onChange={(event) => setAnswers((current) => ({ ...current, [question.id]: event.target.value }))} className="min-w-0 flex-1 rounded-lg border border-zinc-700 bg-zinc-950 px-3 py-2 text-sm text-zinc-100 outline-none focus:border-amber-500" />
                  <button aria-label="Answer task question" onClick={() => void answer(question.id)} disabled={busy || !(answers[question.id] || "").trim()} className="rounded-lg bg-amber-500 px-3 text-zinc-950 disabled:opacity-40"><Send size={14} /></button>
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

            {Boolean(task?.targets.length) && (
              <section className="rounded-2xl border border-zinc-800 bg-zinc-900 p-4">
                <h2 className="text-sm font-medium text-zinc-100">Targets</h2>
                <div className="mt-2 space-y-2 text-xs text-zinc-400">{task?.targets.map((target) => <div key={target.id}><span className="text-zinc-500">{target.resource_type}</span> · {target.resource_key} {target.mutable ? "(mutable)" : ""}</div>)}</div>
              </section>
            )}
          </aside>
        </div>
      </main>
    </div>
  );
}
