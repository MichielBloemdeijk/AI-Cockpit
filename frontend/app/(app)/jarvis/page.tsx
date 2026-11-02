"use client";

import Link from "next/link";
import { useEffect, useRef, useState } from "react";
import {
  Activity,
  AudioLines,
  Bot,
  ChevronDown,
  ChevronUp,
  History,
  Loader2,
  Mic,
  MicOff,
  Plus,
  Radio,
  Send,
  X,
} from "lucide-react";

import { ChatMessage } from "@/components/ChatMessage";
import { DeveloperLab } from "@/components/jarvis/DeveloperLab";
import { acceptJarvisTextTurn, getJarvisPreferences, updateJarvisPreferences, type JarvisPreferences, type JarvisStateResponse } from "@/lib/api/jarvis";
import { acknowledgePushDelivery } from "@/lib/api/push";
import { answerTaskQuestion, controlTask, type TaskSummary } from "@/lib/api/tasks";
import {
  loadJarvisWakeGateMode,
  resolveJarvisWakeGateMode,
  saveJarvisWakeGateMode,
  type JarvisWakeGateMode,
} from "@/lib/jarvis/benchmark-preferences";
import { useJarvis } from "@/lib/jarvis/use-jarvis";
import { useConversationAutoScroll } from "@/lib/conversations/auto-scroll";
import { ACTIVE_TASK_STATUSES, RETRYABLE_TASK_STATUSES, taskStatusLabel } from "@/lib/task-status";
import { createUuid } from "@/lib/uuid";

function statusTone(status: string): string {
  if (status === "completed") return "border-emerald-500/30 bg-emerald-500/10 text-emerald-300";
  if (["waiting_for_worker", "waiting_for_input", "waiting_until"].includes(status)) return "border-amber-500/30 bg-amber-500/10 text-amber-200";
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
  const [draft, setDraft] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [wakeGateMode, setWakeGateMode] = useState<JarvisWakeGateMode>("transcript_rules");
  const [statusOpen, setStatusOpen] = useState(false);
  const [mobilePanel, setMobilePanel] = useState<"tasks" | "activity" | null>(null);
  const [preferences, setPreferences] = useState<JarvisPreferences | null>(null);
  const [actionError, setActionError] = useState<string | null>(null);
  const remoteAudioRef = useRef<HTMLAudioElement | null>(null);
  const conversationScrollRef = useRef<HTMLDivElement | null>(null);
  const wakeGateRef = useRef({ developerMode: false, selected: wakeGateMode });
  wakeGateRef.current = { developerMode: preferences?.developer_mode === true, selected: wakeGateMode };

  const { feed, voice, feedState, voiceState } = useJarvis({
    remoteAudio: () => remoteAudioRef.current,
    wakeGate: () => wakeGateRef.current,
    onError: setActionError,
  });
  const { state, streams, loading, provisionalUser, provisionalAssistant } = feedState;
  const error = actionError ?? feedState.error;
  const voiceStatus = voiceState?.voice ?? { status: "idle" as const, micEnabled: false, playbackActive: false, error: null };
  const wake = voiceState?.wake ?? { status: "idle" as const, error: null, followUpUntil: null };
  const wakeRuntimeError = voiceState?.wakeRuntime.error ?? null;
  const phoneAudio = voiceState?.phoneAudio ?? null;
  const currentConversationId = state?.current_log?.id ?? null;
  const currentMessages = state?.current_log?.messages ?? [];
  const newestMessageId = currentMessages.length ? currentMessages[currentMessages.length - 1].id : null;

  useConversationAutoScroll(conversationScrollRef, !loading, currentConversationId, newestMessageId);

  useEffect(() => {
    if (!provisionalUser?.text && !provisionalAssistant?.text) return;
    const viewport = conversationScrollRef.current;
    if (viewport) viewport.scrollTop = viewport.scrollHeight;
  }, [provisionalAssistant?.text, provisionalUser?.text]);

  useEffect(() => {
    let active = true;
    void getJarvisPreferences().then((next) => {
      if (!active) return;
      setPreferences(next);
      setWakeGateMode(loadJarvisWakeGateMode());
    }).catch((cause) => { if (active) setActionError(cause instanceof Error ? cause.message : "Unable to load Jarvis preferences"); });
    return () => { active = false; };
  }, []);

  useEffect(() => {
    navigator.serviceWorker?.register("/sw.js", { scope: "/", updateViaCache: "none" }).catch(() => undefined);
    const onMessage = (event: MessageEvent<{ type?: string; payload?: { delivery_id?: string } }>) => {
      if (event.data?.type !== "jarvis-push" && event.data?.type !== "jarvis-open-update") return;
      const deliveryId = event.data.payload?.delivery_id;
      if (deliveryId) void acknowledgePushDelivery(deliveryId, "opened");
      void feed?.refresh();
    };
    navigator.serviceWorker?.addEventListener("message", onMessage);
    return () => navigator.serviceWorker?.removeEventListener("message", onMessage);
  }, [feed]);

  useEffect(() => {
    const params = new URLSearchParams(window.location.search);
    const deliveryId = params.get("delivery");
    if (deliveryId) void acknowledgePushDelivery(deliveryId, "opened");
    if (params.has("update")) void feed?.refresh();
  }, [feed]);

  const run = async (action: () => Promise<unknown>) => {
    if (submitting) return;
    setSubmitting(true);
    setActionError(null);
    try {
      await action();
      await feed?.refresh();
    } catch (cause) {
      setActionError(cause instanceof Error ? cause.message : "Jarvis action failed");
    } finally {
      setSubmitting(false);
    }
  };

  const handleSend = async () => {
    const content = draft.trim();
    if (!content) return;
    await run(async () => {
      await acceptJarvisTextTurn(content, createUuid());
      voice?.noteActivity();
      setDraft("");
    });
  };

  const currentLog = state?.current_log ?? null;
  const session = state?.session ?? null;
  const sessionReady = session?.lifecycle_state === "ready";
  const ownsController = voice?.ownsSession(session) ?? false;
  const voiceBusy = voiceStatus.status === "connecting";
  const voiceActive = !["idle", "closed", "error"].includes(voiceStatus.status);
  const effectiveWakeGateMode = resolveJarvisWakeGateMode(preferences?.developer_mode === true, wakeGateMode);
  const degradedComponents = Object.entries(state?.component_health ?? {}).filter(
    ([name, component]) => name !== "worker" && !["healthy", "online"].includes(component.status),
  );
  const componentName = (name: string) => name.replaceAll("_", " ");
  const degradedNames = degradedComponents
    .slice(0, 2)
    .map(([name]) => componentName(name))
    .join(", ");

  const toggleVoice = async () => {
    if (!voice) throw new Error("Voice controller is unavailable");
    if (voiceActive) await voice.sleep(session);
    else await voice.listen(Boolean(currentLog));
  };

  if (loading) {
    return <div className="flex h-full items-center justify-center bg-zinc-950 text-zinc-400"><Loader2 className="animate-spin" /></div>;
  }

  return (
    <div className="flex h-0 min-h-0 flex-1 flex-col overflow-hidden bg-zinc-950 text-zinc-100">
      <audio
        ref={remoteAudioRef}
        autoPlay
        className="hidden"
        aria-hidden="true"
      />
      <header className="shrink-0 border-b border-zinc-800 bg-zinc-900/95 px-3 py-2 md:px-5 md:py-3">
        <div className="md:hidden">
          <button
            type="button"
            onClick={() => setStatusOpen((value) => !value)}
            className="flex min-h-12 w-full items-center justify-between rounded-2xl px-2 text-left"
            aria-expanded={statusOpen}
          >
            <span className="flex min-w-0 items-center gap-3">
              <span className={`h-2.5 w-2.5 rounded-full ${sessionReady || voiceStatus.status === "connected" ? "bg-emerald-400" : "bg-zinc-600"}`} />
              <span className="min-w-0">
                <span className="block truncate text-sm font-semibold">Jarvis</span>
                <span className="block truncate text-xs capitalize text-zinc-400">
                  {floorLabel(state)}{degradedComponents.length ? ` · ${degradedNames}` : ""}
                </span>
              </span>
            </span>
            {statusOpen ? <ChevronUp size={18} className="text-zinc-400" /> : <ChevronDown size={18} className="text-zinc-400" />}
          </button>
          {statusOpen && (
            <div className="mb-2 grid grid-cols-2 gap-2 rounded-2xl border border-zinc-800 bg-zinc-950 p-3 text-xs">
              <div><span className="text-zinc-500">Voice</span><p className="mt-1 capitalize text-zinc-200">{preferences?.voice ?? "marin"} · GPT-Live</p></div>
              <div><span className="text-zinc-500">Wake</span><p className="mt-1 capitalize text-zinc-200">{wake.status.replaceAll("_", " ")}</p></div>
              <div><span className="text-zinc-500">Worker</span><p className={`mt-1 capitalize ${state?.worker_health.status === "online" ? "text-emerald-300" : "text-amber-200"}`}>{state?.worker_health.status ?? "offline"}</p></div>
              <div><span className="text-zinc-500">Systems</span><p className={`mt-1 ${degradedComponents.length ? "text-amber-200" : "text-emerald-300"}`}>{degradedComponents.length ? `${degradedComponents.length} degraded` : "Healthy"}</p></div>
              {!!degradedComponents.length && (
                <div className="col-span-2 space-y-1 border-t border-zinc-800 pt-2">
                  {degradedComponents.map(([name, component]) => (
                    <p key={name} className="text-amber-200">
                      <span className="capitalize">{componentName(name)}</span>: {component.detail || component.status}
                    </p>
                  ))}
                </div>
              )}
              <button
                type="button"
                disabled={!preferences || submitting}
                onClick={() => void run(async () => {
                  if (!preferences) return;
                  const next = await updateJarvisPreferences({ voice: preferences.voice === "marin" ? "cedar" : "marin" });
                  setPreferences(next);
                })}
                className="col-span-2 min-h-12 rounded-xl border border-zinc-700 text-zinc-200 disabled:opacity-40"
              >
                Use {preferences?.voice === "cedar" ? "Marin" : "Cedar"} voice
              </button>
            </div>
          )}
        </div>
        <div className="hidden flex-wrap items-center justify-between gap-3 md:flex">
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
              <span className="text-xs text-zinc-500">
                GPT-Live
              </span>
              {!!degradedComponents.length && (
                <span className="text-xs text-amber-300" title={degradedComponents.map(([name, value]) => `${name}: ${value.detail || value.status}`).join("\n")}>
                  {degradedNames}{degradedComponents.length > 2 ? ` +${degradedComponents.length - 2}` : ""}
                </span>
              )}
              {voiceStatus.status !== "idle" && (
                <span className={voiceStatus.status === "connected" ? "text-xs text-emerald-400" : voiceStatus.status === "error" ? "text-xs text-amber-300" : "text-xs text-violet-300"}>
                  voice {voiceStatus.status.replaceAll("_", " ")}
                </span>
              )}
              <span
                title={wake.error || wakeRuntimeError || undefined}
                className={wake.status === "armed" ? "text-xs text-emerald-400" : wake.status === "fallback" ? "text-xs text-amber-300" : "text-xs text-zinc-500"}
              >
                {voiceState?.wakeModelConfigured || phoneAudio
                  ? `wake ${wake.status.replaceAll("_", " ")}`
                  : "wake model pending"}
              </span>
              <span className="text-[11px] text-zinc-500">
                gate {preferences ? `${wakeGateMode} → ${effectiveWakeGateMode}` : "initializing"}
              </span>
            </div>
            <p className="mt-1 text-xs text-zinc-500">
              {session ? `${ownsController ? "Controller" : "Observer"} · generation ${session.generation} · ${session.provider_adapter}` : "No live controller"}
              {state ? ` · operational day ${state.operational_day}` : ""}
            </p>
          </div>
          <div className="flex flex-wrap items-center gap-2">
            <button
              type="button"
              onClick={() => void run(toggleVoice)}
              disabled={submitting || (phoneAudio ? phoneAudio.mode === "stopping" : voiceBusy || (!voiceActive && !currentLog))}
              aria-pressed={voiceActive}
              className={`inline-flex items-center gap-1.5 rounded-xl border px-3 py-2 text-xs disabled:opacity-40 ${
                voiceActive
                  ? "border-zinc-700 text-zinc-300"
                  : "border-violet-500/30 bg-violet-500/10 text-violet-200"
              }`}
            >
              {voiceActive ? <MicOff size={13} /> : <Mic size={13} />}
              {phoneAudio ? voiceActive ? "Stop Live" : "Listen now" : voiceActive ? "Sleep" : "Arm"}
            </button>
            {phoneAudio && !["waiting", "wake_starting", "stopping"].includes(phoneAudio.mode) && (
              <button type="button" disabled={submitting} onClick={() => void run(async () => { await voice?.wakePhone(); })} className="rounded-xl border border-zinc-700 px-3 py-2 text-xs text-zinc-300 disabled:opacity-40">Hey Jarvis</button>
            )}
            {phoneAudio && <span className="text-xs text-zinc-400">{phoneAudio.text}</span>}
            <Link href="/chat" className="inline-flex items-center gap-1.5 rounded-xl border border-zinc-700 px-3 py-2 text-xs text-zinc-300">
              <History size={13} /> History
            </Link>
            <button
              type="button"
              onClick={() => void run(async () => { await feed?.freshLog(); })}
              disabled={submitting}
              className="inline-flex items-center gap-1.5 rounded-xl border border-zinc-700 px-3 py-2 text-xs text-zinc-300 disabled:opacity-40"
            >
              <Plus size={13} /> Fresh log
            </button>
          </div>
        </div>
      </header>

      {preferences?.developer_mode && voice && (
        <DeveloperLab
          session={session}
          hasLog={Boolean(currentLog)}
          busy={submitting}
          wakeGateMode={wakeGateMode}
          wakeGateLoaded={preferences !== null}
          onWakeGateMode={(mode) => { setWakeGateMode(mode); saveJarvisWakeGateMode(mode); }}
          onSupersede={() => voice.supersede()}
          run={(action) => void run(action)}
        />
      )}
      <main className="relative flex min-h-0 flex-1 overflow-hidden bg-zinc-800 xl:grid xl:grid-cols-[minmax(0,1.35fr)_minmax(17rem,.75fr)_minmax(17rem,.8fr)] xl:gap-px">
        <section className="flex min-h-0 min-w-0 flex-1 flex-col bg-zinc-950">
          <div className="flex items-center justify-between border-b border-zinc-800 px-4 py-3">
            <div className="flex items-center gap-2 text-sm font-medium"><Radio size={15} className="text-violet-400" /> Conversation</div>
            <span className="hidden text-[11px] text-zinc-500 md:inline">cursor {streams.cursors.conversation}</span>
            <div className="flex items-center gap-2 xl:hidden">
              <button type="button" onClick={() => setMobilePanel("tasks")} className="min-h-12 rounded-xl border border-zinc-700 px-3 text-xs text-zinc-200">
                Tasks {state?.tasks.length ? `· ${state.tasks.length}` : ""}
              </button>
              <button type="button" onClick={() => setMobilePanel("activity")} className="min-h-12 rounded-xl border border-zinc-700 px-3 text-xs text-zinc-200">
                Activity {streams.activity.length ? `· ${streams.activity.length}` : ""}
              </button>
            </div>
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
              {provisionalUser?.text && (
                <ChatMessage
                  key={`provisional-user-${provisionalUser.id}`}
                  message={{
                    id: `provisional-user-${provisionalUser.id}`,
                    role: "user",
                    kind: "message",
                    label: "You",
                    content: provisionalUser.text,
                    createdAt: new Date().toISOString(),
                    branchable: false,
                    badges: ["speaking"],
                  }}
                />
              )}
              {provisionalAssistant?.text && (
                <ChatMessage
                  key={`provisional-${provisionalAssistant.id}`}
                  message={{
                    id: `provisional-${provisionalAssistant.id}`,
                    role: "assistant",
                    kind: "message",
                    label: "Jarvis",
                    content: provisionalAssistant.text,
                    createdAt: new Date().toISOString(),
                    branchable: false,
                    badges: ["speaking"],
                  }}
                />
              )}
              {error && <div className="rounded-xl border border-rose-500/30 bg-rose-500/10 px-4 py-3 text-sm text-rose-200">{error}</div>}
            </div>
          </div>
          <div className="mobile-safe-jarvis-input shrink-0 border-t border-zinc-800 bg-zinc-900 p-3">
            <div className="mx-auto flex max-w-3xl items-end gap-2">
              <button
                type="button"
                onClick={() => void run(toggleVoice)}
                disabled={submitting || voiceBusy || (!voiceActive && !currentLog)}
                aria-label={voiceActive ? "Put Jarvis to sleep" : "Arm Jarvis"}
                aria-pressed={voiceActive}
                title={voiceActive ? "Sleep" : "Arm"}
                className={`flex h-12 w-12 shrink-0 items-center justify-center rounded-2xl border disabled:opacity-40 md:hidden ${
                  voiceActive
                    ? "border-zinc-700 text-zinc-300"
                    : "border-violet-500/30 bg-violet-500/10 text-violet-200"
                }`}
              >
                {voiceActive ? <MicOff size={17} /> : <Mic size={17} />}
              </button>
              <textarea
                value={draft}
                onChange={(event) => setDraft(event.target.value)}
                onKeyDown={(event) => {
                  if (event.key === "Enter" && !event.shiftKey) {
                    event.preventDefault();
                    void handleSend();
                  }
                }}
                placeholder="Ask Jarvis anything"
                disabled={submitting}
                rows={1}
                className="max-h-40 min-h-12 flex-1 resize-none overflow-y-hidden rounded-2xl border border-zinc-700 bg-zinc-950 px-4 py-3 text-sm outline-none focus:border-violet-500 disabled:opacity-50"
              />
              <button
                type="button"
                onClick={() => void handleSend()}
                disabled={!draft.trim() || submitting}
                aria-label="Send to Jarvis"
                className="flex h-12 w-12 items-center justify-center rounded-2xl bg-violet-600 text-white disabled:opacity-40"
              >
                {submitting ? <Loader2 size={17} className="animate-spin" /> : <Send size={17} />}
              </button>
            </div>
          </div>
        </section>

        <section className="hidden min-h-0 overflow-y-auto bg-zinc-950 xl:block">
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
                <p className="text-xs font-medium text-amber-200">{state.pending_presentations.length} pending update{state.pending_presentations.length === 1 ? "" : "s"}</p>
              </div>
            )}
          </div>
        </section>

        <section className="hidden min-h-0 overflow-y-auto bg-zinc-950 xl:block">
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

          </div>
        </section>
      </main>
      {mobilePanel && (
        <div className="fixed inset-0 z-40 bg-black/60 xl:hidden" onClick={() => setMobilePanel(null)}>
          <section
            role="dialog"
            aria-label={mobilePanel === "tasks" ? "Jarvis tasks" : "Jarvis activity"}
            className="mobile-safe-jarvis-panel absolute inset-x-0 max-h-[70dvh] overflow-hidden rounded-t-3xl border-t border-zinc-700 bg-zinc-950 shadow-2xl"
            onClick={(event) => event.stopPropagation()}
          >
            <div className="flex min-h-14 items-center justify-between border-b border-zinc-800 px-4">
              <div className="flex items-center gap-2 text-sm font-semibold">
                {mobilePanel === "tasks" ? <Bot size={17} className="text-sky-400" /> : <Activity size={17} className="text-emerald-400" />}
                {mobilePanel === "tasks" ? "Tasks" : "Activity"}
              </div>
              <button type="button" aria-label="Close panel" onClick={() => setMobilePanel(null)} className="flex h-12 w-12 items-center justify-center rounded-2xl text-zinc-400 hover:bg-zinc-900">
                <X size={20} />
              </button>
            </div>
            <div className="max-h-[calc(70dvh-3.5rem)] space-y-3 overflow-y-auto p-3">
              {mobilePanel === "tasks" ? (
                !state?.tasks.length ? <p className="py-12 text-center text-sm text-zinc-600">No active tasks</p> : state.tasks.map((task) => (
                  <TaskCard
                    key={task.id}
                    task={task}
                    question={state.pending_questions.find((item) => item.task_id === task.id)}
                    disabled={submitting}
                    onAction={(action) => void run(action)}
                  />
                ))
              ) : (
                !streams.activity.length ? <p className="py-12 text-center text-sm text-zinc-600">No activity yet</p> : [...streams.activity].reverse().map((item) => (
                  <div key={item.key} className="rounded-2xl border border-zinc-800 bg-zinc-900 p-3">
                    <div className="flex items-center justify-between gap-2 text-[10px] uppercase tracking-wider text-zinc-500">
                      <span>{item.domain}</span><span>{new Date(item.createdAt).toLocaleTimeString()}</span>
                    </div>
                    <p className="mt-1 text-xs text-zinc-300">{activitySummary(item.eventType, item.payload)}</p>
                  </div>
                ))
              )}
            </div>
          </section>
        </div>
      )}
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
  // The same revision-checked command as the task page, so a stale card cannot act on a newer run.
  const control = (action: "cancel" | "retry") => controlTask(task.id, action, {}, task.current_run_id, task.control_revision);
  const controlDisabled = disabled || Boolean(task.pending_control_id);
  return (
    <div className="rounded-2xl border border-zinc-800 bg-zinc-900 p-3">
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0">
          <Link href={`/tasks/${task.id}`} className="line-clamp-2 text-sm font-medium text-zinc-100 hover:text-sky-300">{task.goal}</Link>
          <div className="mt-2 flex flex-wrap items-center gap-2">
            <span className={`rounded-full border px-2 py-0.5 text-[10px] capitalize ${statusTone(task.status)}`}>{taskStatusLabel(task)}</span>
            {task.queue_position ? <span className="text-[10px] text-zinc-500">queue #{task.queue_position}</span> : null}
          </div>
        </div>
        {ACTIVE_TASK_STATUSES.has(task.status) && (
          <button type="button" disabled={controlDisabled} onClick={() => onAction(() => control("cancel"))} className="min-h-12 rounded-xl px-3 text-[11px] text-rose-300 disabled:opacity-40">Cancel</button>
        )}
        {RETRYABLE_TASK_STATUSES.has(task.status) && (task.retry_requires_change_acknowledgement
          // Accepting changed shared resources needs the owner's reason, given on the task page.
          ? <Link href={`/tasks/${task.id}`} className="flex min-h-12 items-center rounded-xl px-3 text-[11px] text-amber-300">Review</Link>
          : <button type="button" disabled={controlDisabled} onClick={() => onAction(() => control("retry"))} className="min-h-12 rounded-xl px-3 text-[11px] text-sky-300 disabled:opacity-40">Retry</button>)}
      </div>
      {task.terminal_summary && <p className="mt-2 line-clamp-3 text-xs text-zinc-400">{task.terminal_summary}</p>}
      {task.status_reason && <p className="mt-2 text-xs text-amber-200">{task.status_reason}</p>}
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
