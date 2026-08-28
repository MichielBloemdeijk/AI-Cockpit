"use client";

import Link from "next/link";
import { useCallback, useEffect, useRef, useState } from "react";
import {
  Activity,
  AudioLines,
  Bot,
  FlaskConical,
  History,
  Loader2,
  Mic,
  MicOff,
  Plus,
  Radio,
  Send,
} from "lucide-react";

import { ChatMessage } from "@/components/ChatMessage";
import {
  acknowledgePushDelivery,
  acceptJarvisTextTurn,
  answerTaskQuestion,
  cancelTask,
  closeJarvisRealtimeSession,
  createJarvisRealtimeCall,
  getJarvisLogEvents,
  getJarvisSessionEvents,
  getJarvisState,
  getTaskEvents,
  injectFakeJarvisEvent,
  reserveJarvisPresentations,
  retryTask,
  startFreshJarvisLog,
  type JarvisFakeProviderEventRequest,
  type JarvisStateResponse,
  type TaskSummary,
} from "@/lib/api";
import {
  EMPTY_JARVIS_LIVE_STREAMS,
  mergeJarvisDomainReplay,
  resetJarvisConversationStream,
  resetJarvisSessionStream,
  type JarvisLiveStreamState,
} from "@/lib/jarvis-live-state";
import {
  JarvisRealtimeController,
  type JarvisRealtimeSnapshot,
} from "@/lib/jarvis-realtime";
import { useConversationAutoScroll } from "@/lib/conversation-auto-scroll";


const ACTIVE_TASKS = new Set(["queued", "running", "waiting_for_input"]);

function statusTone(status: string): string {
  if (status === "completed") return "border-emerald-500/30 bg-emerald-500/10 text-emerald-300";
  if (status === "waiting_for_input") return "border-amber-500/30 bg-amber-500/10 text-amber-200";
  if (["failed", "interrupted", "cancelled"].includes(status)) {
    return "border-rose-500/30 bg-rose-500/10 text-rose-300";
  }
  return "border-sky-500/30 bg-sky-500/10 text-sky-300";
}

function floorLabel(state: JarvisStateResponse | null): string {
  if (!state?.session) return "text fallback";
  return state.session.floor_state.replaceAll("_", " ");
}

function activitySummary(eventType: string, payload: Record<string, unknown> | null): string {
  const summary = payload?.summary ?? payload?.question ?? payload?.content ?? payload?.reason;
  return typeof summary === "string" ? summary : eventType.replaceAll(".", " · ");
}

export default function JarvisPage() {
  const [state, setState] = useState<JarvisStateResponse | null>(null);
  const [streams, setStreams] = useState<JarvisLiveStreamState>(EMPTY_JARVIS_LIVE_STREAMS);
  const [draft, setDraft] = useState("");
  const [loading, setLoading] = useState(true);
  const [submitting, setSubmitting] = useState(false);
  const [labOpen, setLabOpen] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [realtime, setRealtime] = useState<JarvisRealtimeSnapshot>({
    status: "idle",
    attempt: 0,
    session: null,
    error: null,
  });
  const streamsRef = useRef<JarvisLiveStreamState>(EMPTY_JARVIS_LIVE_STREAMS);
  const logIdRef = useRef<string | null>(null);
  const sessionIdRef = useRef<string | null>(null);
  const controllerIdRef = useRef("");
  const realtimeControllerRef = useRef<JarvisRealtimeController | null>(null);
  const remoteAudioRef = useRef<HTMLAudioElement | null>(null);
  const conversationScrollRef = useRef<HTMLDivElement | null>(null);
  const currentConversationId = state?.current_log?.id ?? null;
  const currentMessages = state?.current_log?.messages ?? [];
  const newestMessageId = currentMessages.length
    ? currentMessages[currentMessages.length - 1].id
    : null;

  useConversationAutoScroll(
    conversationScrollRef,
    !loading,
    currentConversationId,
    newestMessageId,
  );

  const setLiveStreams = useCallback((next: JarvisLiveStreamState) => {
    streamsRef.current = next;
    setStreams(next);
  }, []);

  const refresh = useCallback(async () => {
    try {
      const projection = await getJarvisState();
      const logId = projection.current_log?.id ?? null;
      let nextStreams = streamsRef.current;
      if (logIdRef.current !== logId) {
        nextStreams = resetJarvisConversationStream(nextStreams);
        logIdRef.current = logId;
      }
      const sessionId = projection.session?.id ?? null;
      if (sessionIdRef.current !== sessionId) {
        nextStreams = resetJarvisSessionStream(nextStreams);
        sessionIdRef.current = sessionId;
      }
      const [conversationReplay, taskReplay, sessionReplay] = await Promise.all([
        logId ? getJarvisLogEvents(logId, nextStreams.cursors.conversation) : Promise.resolve(null),
        getTaskEvents(nextStreams.cursors.task),
        projection.session
          ? getJarvisSessionEvents(projection.session.id, nextStreams.cursors.session)
          : Promise.resolve(null),
      ]);
      if (conversationReplay) {
        nextStreams = mergeJarvisDomainReplay(nextStreams, "conversation", conversationReplay.events);
      }
      nextStreams = mergeJarvisDomainReplay(nextStreams, "task", taskReplay.events);
      if (sessionReplay) {
        nextStreams = mergeJarvisDomainReplay(nextStreams, "session", sessionReplay.events);
      }
      setLiveStreams(nextStreams);
      setState(projection);
      setError(null);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "Unable to load Jarvis");
    } finally {
      setLoading(false);
    }
  }, [setLiveStreams]);

  useEffect(() => {
    controllerIdRef.current = `jarvis-browser-${crypto.randomUUID()}`;
    realtimeControllerRef.current = new JarvisRealtimeController({
      controllerId: controllerIdRef.current,
      onSnapshot: setRealtime,
      onRemoteStream: (stream) => {
        const audio = remoteAudioRef.current;
        if (!audio) return;
        audio.srcObject = stream;
        if (stream) void audio.play().catch(() => undefined);
      },
    });
    void refresh();
    const timer = window.setInterval(() => void refresh(), 1500);
    return () => {
      window.clearInterval(timer);
      void realtimeControllerRef.current?.stop();
      realtimeControllerRef.current = null;
    };
  }, [refresh]);

  useEffect(() => {
    navigator.serviceWorker
      ?.register("/sw.js", { scope: "/", updateViaCache: "none" })
      .catch(() => undefined);
    const onMessage = (
      event: MessageEvent<{
        type?: string;
        payload?: { delivery_id?: string };
      }>,
    ) => {
      if (
        event.data?.type !== "jarvis-push"
        && event.data?.type !== "jarvis-open-update"
      ) return;
      const deliveryId = event.data.payload?.delivery_id;
      if (deliveryId) void acknowledgePushDelivery(deliveryId, "opened");
      void refresh();
    };
    navigator.serviceWorker?.addEventListener("message", onMessage);
    return () => navigator.serviceWorker?.removeEventListener("message", onMessage);
  }, [refresh]);

  useEffect(() => {
    const params = new URLSearchParams(window.location.search);
    const deliveryId = params.get("delivery");
    if (deliveryId) void acknowledgePushDelivery(deliveryId, "opened");
    if (params.has("update")) void refresh();
  }, [refresh]);

  const run = async (action: () => Promise<unknown>) => {
    if (submitting) return;
    setSubmitting(true);
    setError(null);
    try {
      await action();
      await refresh();
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "Jarvis action failed");
    } finally {
      setSubmitting(false);
    }
  };

  const handleSend = async () => {
    const content = draft.trim();
    if (!content) return;
    await run(async () => {
      await acceptJarvisTextTurn(content, crypto.randomUUID());
      setDraft("");
    });
  };

  const inject = async (
    kind: JarvisFakeProviderEventRequest["kind"],
    payload: Record<string, unknown> = {},
  ) => {
    const liveSession = state?.session;
    if (!liveSession) throw new Error("Start a fake controller first");
    await injectFakeJarvisEvent(liveSession.id, {
      generation: liveSession.generation,
      event_id: `lab-${crypto.randomUUID()}`,
      kind,
      payload,
    });
  };

  const presentPending = async () => {
    const liveSession = state?.session;
    if (!liveSession) throw new Error("Start a fake controller first");
    const reservation = await reserveJarvisPresentations(liveSession.id, liveSession.generation);
    if (!reservation.presentation_ids.length) throw new Error("No presentation is ready at this boundary");
    await inject("assistant_response", {
      response_id: `lab-presentation-${crypto.randomUUID()}`,
      item_id: `lab-presentation-item-${crypto.randomUUID()}`,
      transcript: reservation.facts
        .map((fact) => String(fact.summary ?? fact.question ?? fact.status ?? "Task update"))
        .join(" "),
      presentation_ids: reservation.presentation_ids,
    });
  };

  const currentLog = state?.current_log ?? null;
  const session = state?.session ?? null;
  const sessionReady = session?.lifecycle_state === "ready";
  const ownsController = session?.controller_id === controllerIdRef.current || session?.controller_id === "typed-bridge";
  const realtimeBusy = realtime.status === "connecting" || realtime.status === "reconnecting";

  if (loading) {
    return <div className="flex h-full items-center justify-center bg-zinc-950 text-zinc-400"><Loader2 className="animate-spin" /></div>;
  }

  return (
    <div className="flex h-full min-h-0 flex-col bg-zinc-950 text-zinc-100">
      <audio ref={remoteAudioRef} autoPlay className="hidden" aria-hidden="true" />
      <header className="border-b border-zinc-800 bg-zinc-900/95 px-3 py-3 md:px-5">
        <div className="flex flex-wrap items-center justify-between gap-3">
          <div className="min-w-0">
            <div className="flex flex-wrap items-center gap-2">
              <AudioLines size={18} className="text-violet-400" />
              <h1 className="max-w-72 truncate text-sm font-semibold">{currentLog?.title || "Jarvis"}</h1>
              <span className={`rounded-full border px-2 py-0.5 text-[11px] ${sessionReady ? "border-violet-500/30 bg-violet-500/10 text-violet-300" : "border-zinc-700 text-zinc-500"}`}>
                {floorLabel(state)}
              </span>
              <span className={state?.worker_health.status === "online" ? "text-xs text-emerald-400" : "text-xs text-zinc-500"}>
                worker {state?.worker_health.status || "offline"}
              </span>
              {realtime.status !== "idle" && (
                <span className={realtime.status === "connected" ? "text-xs text-emerald-400" : realtime.status === "text_fallback" ? "text-xs text-amber-300" : "text-xs text-violet-300"}>
                  voice {realtime.status.replaceAll("_", " ")}{realtimeBusy ? ` · attempt ${realtime.attempt}` : ""}
                </span>
              )}
            </div>
            <p className="mt-1 text-xs text-zinc-500">
              {session ? `${ownsController ? "Controller" : "Observer"} · generation ${session.generation} · ${session.provider_adapter}` : "No live controller"}
              {state ? ` · operational day ${state.operational_day}` : ""}
            </p>
          </div>
          <div className="flex flex-wrap items-center gap-2">
            <button
              type="button"
              onClick={() => void run(async () => {
                if (!currentLog) throw new Error("Send the first turn before arming Jarvis");
                if (!realtimeControllerRef.current) throw new Error("Realtime controller is unavailable");
                await realtimeControllerRef.current.start();
              })}
              disabled={submitting || realtimeBusy || realtime.status === "connected" || !currentLog}
              className="inline-flex items-center gap-1.5 rounded-xl border border-violet-500/30 bg-violet-500/10 px-3 py-2 text-xs text-violet-200 disabled:opacity-40"
            >
              <Mic size={13} /> Arm
            </button>
            <button
              type="button"
              onClick={() => void run(async () => {
                if (realtime.status !== "idle") {
                  await realtimeControllerRef.current?.stop();
                } else if (session) {
                  await closeJarvisRealtimeSession(session.id, session.generation);
                }
              })}
              disabled={submitting || (!sessionReady && realtime.status === "idle")}
              className="inline-flex items-center gap-1.5 rounded-xl border border-zinc-700 px-3 py-2 text-xs text-zinc-300 disabled:opacity-40"
            >
              <MicOff size={13} /> Sleep
            </button>
            <Link href="/chat" className="inline-flex items-center gap-1.5 rounded-xl border border-zinc-700 px-3 py-2 text-xs text-zinc-300">
              <History size={13} /> History
            </Link>
            <button
              type="button"
              onClick={() => void run(async () => {
                await startFreshJarvisLog(crypto.randomUUID());
                setLiveStreams(resetJarvisConversationStream(streamsRef.current));
              })}
              disabled={submitting}
              className="inline-flex items-center gap-1.5 rounded-xl border border-zinc-700 px-3 py-2 text-xs text-zinc-300 disabled:opacity-40"
            >
              <Plus size={13} /> Fresh log
            </button>
          </div>
        </div>
      </header>

      <main className="grid min-h-0 flex-1 gap-px overflow-y-auto bg-zinc-800 xl:grid-cols-[minmax(0,1.35fr)_minmax(17rem,.75fr)_minmax(17rem,.8fr)] xl:overflow-hidden">
        <section className="flex min-h-[28rem] min-w-0 flex-col bg-zinc-950">
          <div className="flex items-center justify-between border-b border-zinc-800 px-4 py-3">
            <div className="flex items-center gap-2 text-sm font-medium"><Radio size={15} className="text-violet-400" /> Conversation</div>
            <span className="text-[11px] text-zinc-500">cursor {streams.cursors.conversation}</span>
          </div>
          <div ref={conversationScrollRef} className="min-h-0 flex-1 overflow-y-auto px-3 py-5 md:px-6">
            <div className="mx-auto max-w-3xl space-y-3">
              {!currentLog?.messages.length ? (
                <div className="flex min-h-80 flex-col items-center justify-center text-center text-zinc-500">
                  <div className="flex h-16 w-16 items-center justify-center rounded-2xl border border-violet-500/20 bg-violet-500/10"><AudioLines size={28} className="text-violet-400" /></div>
                  <h2 className="mt-4 text-base font-semibold text-zinc-200">Jarvis is ready</h2>
                  <p className="mt-2 max-w-sm text-sm">The first durable turn creates today&apos;s log and starts the deterministic fake provider.</p>
                </div>
              ) : currentLog.messages.map((message) => (
                <ChatMessage
                  key={message.id}
                  message={{
                    id: message.id,
                    role: message.role,
                    kind: "message",
                    label: message.author_label || undefined,
                    content: message.content,
                    createdAt: message.created_at,
                    branchable: false,
                    badges: message.communication_state && message.communication_state !== "completed" ? [message.communication_state] : undefined,
                  }}
                />
              ))}
              {error && <div className="rounded-xl border border-rose-500/30 bg-rose-500/10 px-4 py-3 text-sm text-rose-200">{error}</div>}
            </div>
          </div>
          <div className="border-t border-zinc-800 bg-zinc-900 p-3">
            <div className="mx-auto flex max-w-3xl items-end gap-2">
              <textarea
                value={draft}
                onChange={(event) => setDraft(event.target.value)}
                onKeyDown={(event) => {
                  if (event.key === "Enter" && !event.shiftKey) {
                    event.preventDefault();
                    void handleSend();
                  }
                }}
                placeholder="Talk to Jarvis — try “delegate: prepare a brief”"
                disabled={submitting}
                rows={1}
                className="max-h-40 min-h-11 flex-1 resize-none rounded-2xl border border-zinc-700 bg-zinc-950 px-4 py-3 text-sm outline-none focus:border-violet-500 disabled:opacity-50"
              />
              <button
                type="button"
                onClick={() => void handleSend()}
                disabled={!draft.trim() || submitting}
                aria-label="Send to Jarvis"
                className="flex h-11 w-11 items-center justify-center rounded-2xl bg-violet-600 text-white disabled:opacity-40"
              >
                {submitting ? <Loader2 size={17} className="animate-spin" /> : <Send size={17} />}
              </button>
            </div>
          </div>
        </section>

        <section className="min-h-0 overflow-y-auto bg-zinc-950">
          <div className="sticky top-0 z-10 flex items-center justify-between border-b border-zinc-800 bg-zinc-950/95 px-4 py-3">
            <div className="flex items-center gap-2 text-sm font-medium"><Bot size={15} className="text-sky-400" /> Tasks</div>
            <span className="text-[11px] text-zinc-500">cursor {streams.cursors.task}</span>
          </div>
          <div className="space-y-3 p-3">
            {!state?.tasks.length ? <p className="py-10 text-center text-sm text-zinc-600">No active tasks</p> : state.tasks.map((task) => (
              <TaskCard
                key={task.id}
                task={task}
                question={state.pending_questions.find((item) => item.task_id === task.id)}
                disabled={submitting}
                onAction={(action) => void run(action)}
              />
            ))}
            {!!state?.pending_presentations.length && (
              <div className="rounded-xl border border-amber-500/20 bg-amber-500/5 p-3">
                <p className="text-xs font-medium text-amber-200">{state.pending_presentations.length} update{state.pending_presentations.length === 1 ? "" : "s"} waiting for a safe boundary</p>
              </div>
            )}
          </div>
        </section>

        <section className="min-h-0 overflow-y-auto bg-zinc-950">
          <div className="sticky top-0 z-10 flex items-center justify-between border-b border-zinc-800 bg-zinc-950/95 px-4 py-3">
            <div className="flex items-center gap-2 text-sm font-medium"><Activity size={15} className="text-emerald-400" /> Activity</div>
            <span className="text-[11px] text-zinc-500">session {streams.cursors.session}</span>
          </div>
          <div className="space-y-2 p-3">
            {!streams.activity.length ? <p className="py-10 text-center text-sm text-zinc-600">No activity yet</p> : [...streams.activity].reverse().map((item) => (
              <div key={item.key} className="rounded-xl border border-zinc-800 bg-zinc-900 p-3">
                <div className="flex items-center justify-between gap-2">
                  <span className="text-[10px] font-medium uppercase tracking-wider text-zinc-500">{item.domain} · {item.sequence}</span>
                  <span className="text-[10px] text-zinc-600">{new Date(item.createdAt).toLocaleTimeString()}</span>
                </div>
                <p className="mt-1 text-xs text-zinc-300">{activitySummary(item.eventType, item.payload)}</p>
              </div>
            ))}

            <div className="mt-4 rounded-2xl border border-dashed border-violet-500/30 bg-violet-500/5">
              <button type="button" onClick={() => setLabOpen((value) => !value)} className="flex w-full items-center justify-between px-3 py-3 text-left text-xs font-medium text-violet-200">
                <span className="flex items-center gap-2"><FlaskConical size={14} /> Developer conversation lab</span>
                <span>{labOpen ? "Hide" : "Show"}</span>
              </button>
              {labOpen && (
                <div className="grid grid-cols-2 gap-2 border-t border-violet-500/20 p-3">
                  <LabButton label="User speaking" disabled={!sessionReady || submitting} onClick={() => void run(() => inject("user_speaking"))} />
                  <LabButton label="Release floor" disabled={!sessionReady || submitting} onClick={() => void run(() => inject("user_stopped"))} />
                  <LabButton label="Direct reply" disabled={!sessionReady || submitting} onClick={() => void run(() => inject("assistant_response", { response_id: `lab-${crypto.randomUUID()}`, item_id: `lab-${crypto.randomUUID()}`, transcript: "This is a deterministic direct response." }))} />
                  <LabButton label="Interrupt reply" disabled={!sessionReady || submitting} onClick={() => void run(() => inject("assistant_interrupted", { response_id: `lab-${crypto.randomUUID()}`, item_id: `lab-${crypto.randomUUID()}`, transcript: "This response was interrupted before it finished.", heard_transcript: "This response was interrupted" }))} />
                  <LabButton label="Delegate demo" disabled={!sessionReady || submitting} onClick={() => void run(() => inject("shell_call", { call_id: `lab-${crypto.randomUUID()}`, name: "dispatch_task", arguments: { goal: "Prepare the developer lab report" } }))} />
                  <LabButton label="Present updates" disabled={!sessionReady || submitting} onClick={() => void run(presentPending)} />
                  <LabButton label="Supersede" disabled={!currentLog || submitting} onClick={() => void run(async () => { controllerIdRef.current = `jarvis-browser-${crypto.randomUUID()}`; await createJarvisRealtimeCall(controllerIdRef.current, "controller"); })} />
                  <LabButton label="Provider error" disabled={!sessionReady || submitting} onClick={() => void run(() => inject("provider_error", { reason: "simulated_failure" }))} />
                </div>
              )}
            </div>
          </div>
        </section>
      </main>
    </div>
  );
}

function TaskCard({
  task,
  question,
  disabled,
  onAction,
}: {
  task: TaskSummary;
  question: JarvisStateResponse["pending_questions"][number] | undefined;
  disabled: boolean;
  onAction: (action: () => Promise<unknown>) => void;
}) {
  return (
    <div className="rounded-2xl border border-zinc-800 bg-zinc-900 p-3">
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0">
          <Link href={`/tasks/${task.id}`} className="line-clamp-2 text-sm font-medium text-zinc-100 hover:text-sky-300">{task.goal}</Link>
          <div className="mt-2 flex flex-wrap items-center gap-2">
            <span className={`rounded-full border px-2 py-0.5 text-[10px] capitalize ${statusTone(task.status)}`}>{task.status.replaceAll("_", " ")}</span>
            {task.queue_position ? <span className="text-[10px] text-zinc-500">queue #{task.queue_position}</span> : null}
          </div>
        </div>
        {ACTIVE_TASKS.has(task.status) && (
          <button type="button" disabled={disabled} onClick={() => onAction(() => cancelTask(task.id))} className="text-[11px] text-rose-300 disabled:opacity-40">Cancel</button>
        )}
        {["failed", "interrupted", "cancelled"].includes(task.status) && (
          <button type="button" disabled={disabled} onClick={() => onAction(() => retryTask(task.id))} className="text-[11px] text-sky-300 disabled:opacity-40">Retry</button>
        )}
      </div>
      {task.terminal_summary && <p className="mt-2 line-clamp-3 text-xs text-zinc-400">{task.terminal_summary}</p>}
      {question && (
        <div className="mt-3 rounded-xl border border-amber-500/20 bg-amber-500/5 p-2.5">
          <p className="text-xs text-amber-100">{question.question}</p>
          <button
            type="button"
            disabled={disabled}
            onClick={() => {
              const answer = window.prompt(question.question);
              if (answer?.trim()) onAction(() => answerTaskQuestion(task.id, question.question_id, answer));
            }}
            className="mt-2 text-[11px] font-medium text-amber-300 disabled:opacity-40"
          >
            Answer
          </button>
        </div>
      )}
    </div>
  );
}

function LabButton({ label, disabled, onClick }: { label: string; disabled: boolean; onClick: () => void }) {
  return (
    <button type="button" disabled={disabled} onClick={onClick} className="rounded-lg border border-zinc-700 bg-zinc-900 px-2 py-2 text-[11px] text-zinc-300 hover:border-violet-500/40 disabled:opacity-40">
      {label}
    </button>
  );
}
