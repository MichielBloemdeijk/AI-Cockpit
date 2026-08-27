"use client";

import Link from "next/link";
import { useCallback, useEffect, useMemo, useState } from "react";
import { Bot, ChevronRight, ListTodo, Loader2, Plus } from "lucide-react";

import { dispatchTask, getTaskWorkerHealth, listTasks, type TaskSummary } from "@/lib/api";

const ACTIVE = new Set(["queued", "running", "waiting_for_input"]);

function statusClass(status: string): string {
  if (status === "completed") return "border-emerald-500/30 bg-emerald-500/10 text-emerald-300";
  if (["failed", "interrupted", "cancelled"].includes(status)) return "border-red-500/30 bg-red-500/10 text-red-300";
  if (status === "waiting_for_input") return "border-amber-500/30 bg-amber-500/10 text-amber-300";
  return "border-blue-500/30 bg-blue-500/10 text-blue-300";
}

export default function TasksPage() {
  const [tasks, setTasks] = useState<TaskSummary[]>([]);
  const [goal, setGoal] = useState("");
  const [showFinished, setShowFinished] = useState(true);
  const [loading, setLoading] = useState(true);
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [workerStatus, setWorkerStatus] = useState("offline");

  const refresh = useCallback(async () => {
    try {
      const [nextTasks, worker] = await Promise.all([listTasks(), getTaskWorkerHealth()]);
      setTasks(nextTasks);
      setWorkerStatus(worker.status);
      setError(null);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "Unable to load tasks");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void refresh();
    const timer = window.setInterval(() => void refresh(), 4000);
    return () => window.clearInterval(timer);
  }, [refresh]);

  const visible = useMemo(
    () => tasks.filter((task) => showFinished || ACTIVE.has(task.status)),
    [showFinished, tasks],
  );

  const create = async () => {
    const normalized = goal.trim();
    if (!normalized || submitting) return;
    setSubmitting(true);
    setError(null);
    try {
      await dispatchTask({ goal: normalized, idempotency_key: crypto.randomUUID(), origin_surface: "tasks_ui" });
      setGoal("");
      await refresh();
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "Unable to dispatch task");
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <div className="flex h-full min-h-0 flex-col bg-zinc-950">
      <header className="border-b border-zinc-800 bg-zinc-900 px-4 py-4 md:px-6">
        <div className="mx-auto flex max-w-5xl items-center justify-between gap-4">
          <div>
            <div className="flex items-center gap-2 text-zinc-100">
              <ListTodo size={19} className="text-blue-400" />
              <h1 className="font-semibold">Tasks</h1>
              <span className="text-xs text-zinc-500">{tasks.filter((task) => ACTIVE.has(task.status)).length} active</span>
              <span className={workerStatus === "online" ? "text-xs text-emerald-400" : "text-xs text-zinc-500"}>worker {workerStatus}</span>
            </div>
            <p className="mt-1 text-xs text-zinc-500">Global background work, independent from conversation history.</p>
          </div>
          <label className="flex items-center gap-2 text-xs text-zinc-400">
            <input type="checkbox" checked={showFinished} onChange={(event) => setShowFinished(event.target.checked)} />
            Finished
          </label>
        </div>
      </header>

      <main className="min-h-0 flex-1 overflow-y-auto p-4 md:p-6">
        <div className="mx-auto max-w-5xl space-y-5">
          <section className="rounded-2xl border border-zinc-800 bg-zinc-900 p-3">
            <div className="flex items-end gap-2">
              <label className="min-w-0 flex-1">
                <span className="mb-2 block text-xs font-medium uppercase tracking-wider text-zinc-500">Dispatch work</span>
                <textarea
                  value={goal}
                  onChange={(event) => setGoal(event.target.value)}
                  onKeyDown={(event) => {
                    if (event.key === "Enter" && !event.shiftKey) {
                      event.preventDefault();
                      void create();
                    }
                  }}
                  rows={2}
                  placeholder="What should the agent accomplish?"
                  className="w-full resize-none rounded-xl border border-zinc-700 bg-zinc-950 px-3 py-2.5 text-sm text-zinc-100 outline-none focus:border-blue-500"
                />
              </label>
              <button
                type="button"
                onClick={() => void create()}
                disabled={!goal.trim() || submitting}
                className="flex h-11 items-center gap-2 rounded-xl bg-blue-600 px-4 text-sm font-medium text-white disabled:opacity-40"
              >
                {submitting ? <Loader2 size={16} className="animate-spin" /> : <Plus size={16} />}
                Dispatch
              </button>
            </div>
          </section>

          {error && <div className="rounded-xl border border-red-500/30 bg-red-500/10 px-4 py-3 text-sm text-red-200">{error}</div>}
          {loading ? (
            <div className="flex justify-center py-16 text-zinc-500"><Loader2 className="animate-spin" /></div>
          ) : visible.length === 0 ? (
            <div className="flex flex-col items-center py-20 text-center text-zinc-500">
              <Bot size={30} />
              <p className="mt-3 text-sm">No tasks yet.</p>
            </div>
          ) : (
            <div className="space-y-2">
              {visible.map((task) => (
                <Link
                  key={task.id}
                  href={`/tasks/${task.id}`}
                  className="flex items-center gap-4 rounded-2xl border border-zinc-800 bg-zinc-900 px-4 py-3 transition-colors hover:border-zinc-700 hover:bg-zinc-800/80"
                >
                  <div className="min-w-0 flex-1">
                    <div className="flex flex-wrap items-center gap-2">
                      <span className="truncate text-sm font-medium text-zinc-100">{task.goal}</span>
                      <span className={`rounded-full border px-2 py-0.5 text-[11px] capitalize ${statusClass(task.status)}`}>
                        {task.status.replaceAll("_", " ")}
                      </span>
                      {task.queue_position && <span className="text-[11px] text-zinc-500">queue #{task.queue_position}</span>}
                      {task.cancellation_requested_at && task.status === "running" && (
                        <span className="text-[11px] text-amber-300">cancelling</span>
                      )}
                    </div>
                    <div className="mt-1 flex gap-3 text-xs text-zinc-500">
                      <span>#{task.creation_order}</span>
                      <span>{task.origin_surface.replaceAll("_", " ")}</span>
                      <span>{new Date(task.created_at).toLocaleString()}</span>
                    </div>
                    {task.terminal_summary && <p className="mt-2 line-clamp-2 text-sm text-zinc-400">{task.terminal_summary}</p>}
                  </div>
                  <ChevronRight size={17} className="shrink-0 text-zinc-600" />
                </Link>
              ))}
            </div>
          )}
        </div>
      </main>
    </div>
  );
}
