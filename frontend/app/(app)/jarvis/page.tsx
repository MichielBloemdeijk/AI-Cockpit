"use client";

import Link from "next/link";
import { useCallback, useEffect, useRef, useState } from "react";
import {
  Activity,
  AudioLines,
  Bot,
  ChevronDown,
  ChevronUp,
  FlaskConical,
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
import {
  acknowledgePushDelivery,
  acceptJarvisTextTurn,
  answerTaskQuestion,
  cancelTask,
  closeJarvisRealtimeSession,
  createJarvisRealtimeCall,
  getJarvisLogEvents,
  getJarvisPreferences,
  getJarvisSessionEvents,
  getJarvisState,
  getTaskEvents,
  jarvisLiveUrl,
  injectFakeJarvisEvent,
  retryTask,
  startFreshJarvisLog,
  updateJarvisPreferences,
  type JarvisFakeProviderEventRequest,
  type JarvisLiveEvent,
  type JarvisPreferences,
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
import {
  configuredJarvisWakeModel,
} from "@/lib/jarvis-wake-protocol";
import {
  JarvisWakeRuntime,
  type JarvisWakeRuntimeSnapshot,
} from "@/lib/jarvis-wake-runtime";
import {
  JarvisWakeLifecycle,
  type JarvisWakeSnapshot,
} from "@/lib/jarvis-wake-state";
import { useConversationAutoScroll } from "@/lib/conversation-auto-scroll";
import { classifyJarvisAddress } from "@/lib/jarvis-address-detection";
import {
  mergeProvisionalTranscript,
  provisionalTranscriptIsPersisted,
  type ProvisionalTranscript,
} from "@/lib/jarvis-transcript-state";
import { ACTIVE_TASK_STATUSES, taskStatusLabel } from "@/lib/task-status";


const WAKE_MODEL_CONFIGURATION = configuredJarvisWakeModel();
const FOLLOW_UP_ACTIVITY_EVENTS = new Set([
  "input_audio_buffer.speech_started",
  "input_audio_buffer.speech_stopped",
  "conversation.item.input_audio_transcription.completed",
  "response.created",
  "response.done",
]);

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
  const [state, setState] = useState<JarvisStateResponse | null>(null);
  const [streams, setStreams] = useState<JarvisLiveStreamState>(EMPTY_JARVIS_LIVE_STREAMS);
  const [draft, setDraft] = useState("");
  const [loading, setLoading] = useState(true);
  const [submitting, setSubmitting] = useState(false);
  const [labOpen, setLabOpen] = useState(false);
  const [statusOpen, setStatusOpen] = useState(false);
  const [mobilePanel, setMobilePanel] = useState<"tasks" | "activity" | null>(null);
  const [preferences, setPreferences] = useState<JarvisPreferences | null>(null);
  const [provisionalUser, setProvisionalUser] = useState<ProvisionalTranscript | null>(null);
  const [provisionalAssistant, setProvisionalAssistant] = useState<ProvisionalTranscript | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [realtime, setRealtime] = useState<JarvisRealtimeSnapshot>({
    status: "idle",
    attempt: 0,
    session: null,
    error: null,
  });
  const [wake, setWake] = useState<JarvisWakeSnapshot>({
    status: "idle",
    error: null,
    followUpUntil: null,
  });
  const [wakeRuntime, setWakeRuntime] = useState<JarvisWakeRuntimeSnapshot>({
    status: "idle",
    error: null,
  });
  const [liveCursorReady, setLiveCursorReady] = useState(false);
  const streamsRef = useRef<JarvisLiveStreamState>(EMPTY_JARVIS_LIVE_STREAMS);
  const logIdRef = useRef<string | null>(null);
  const sessionIdRef = useRef<string | null>(null);
  const controllerIdRef = useRef("");
  const realtimeControllerRef = useRef<JarvisRealtimeController | null>(null);
  const wakeLifecycleRef = useRef<JarvisWakeLifecycle | null>(null);
  const wakeRuntimeRef = useRef<JarvisWakeRuntime | null>(null);
  const refreshInFlightRef = useRef(false);
  const rearmWakeRef = useRef<(reason: "idle_timeout" | "explicit_sleep") => Promise<void>>(
    async () => undefined,
  );
  const remoteAudioRef = useRef<HTMLAudioElement | null>(null);
  const conversationScrollRef = useRef<HTMLDivElement | null>(null);
  const liveCursorRef = useRef(0);
  const liveCursorSeededRef = useRef(false);
  const pendingWakeAddressRef = useRef<{ wakeScore: number; detectedAt: number } | null>(null);
  const newestMessageIdRef = useRef<string | null>(null);
  const currentConversationId = state?.current_log?.id ?? null;
  const currentMessages = state?.current_log?.messages ?? [];
  const newestMessageId = currentMessages.length
    ? currentMessages[currentMessages.length - 1].id
    : null;
  const provisionalUserPersisted = provisionalTranscriptIsPersisted(
    currentMessages,
    provisionalUser,
    "user",
  );
  const provisionalAssistantPersisted = provisionalTranscriptIsPersisted(
    currentMessages,
    provisionalAssistant,
    "assistant",
  );

  useConversationAutoScroll(
    conversationScrollRef,
    !loading,
    currentConversationId,
    newestMessageId,
  );

  useEffect(() => {
    newestMessageIdRef.current = newestMessageId;
  }, [newestMessageId]);

  useEffect(() => {
    if (!provisionalUser?.text && !provisionalAssistant?.text) return;
    const viewport = conversationScrollRef.current;
    if (viewport) viewport.scrollTop = viewport.scrollHeight;
  }, [provisionalAssistant?.text, provisionalUser?.text]);

  useEffect(() => {
    if (provisionalUserPersisted) setProvisionalUser(null);
    if (provisionalAssistantPersisted) setProvisionalAssistant(null);
  }, [provisionalAssistantPersisted, provisionalUserPersisted]);

  const setLiveStreams = useCallback((next: JarvisLiveStreamState) => {
    streamsRef.current = next;
    setStreams(next);
  }, []);

  const refresh = useCallback(async () => {
    if (refreshInFlightRef.current) return;
    refreshInFlightRef.current = true;
    try {
      const projection = await getJarvisState();
      if (!liveCursorSeededRef.current) {
        liveCursorSeededRef.current = true;
        liveCursorRef.current = Math.max(0, projection.live_event_cursor);
        setLiveCursorReady(true);
      }
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
      refreshInFlightRef.current = false;
      setLoading(false);
    }
  }, [setLiveStreams]);

  useEffect(() => {
    let disposed = false;
    controllerIdRef.current = `jarvis-browser-${crypto.randomUUID()}`;
    const realtimeController = new JarvisRealtimeController({
      controllerId: controllerIdRef.current,
      onSnapshot: setRealtime,
      onRemoteStream: (stream) => {
        const audio = remoteAudioRef.current;
        if (!audio) return;
        audio.srcObject = stream;
        if (stream) {
          audio.muted = false;
          void audio.play()
            .then(() => realtimeControllerRef.current?.recordPlaybackSignal(
              "audio_play_resolved",
              `media_current_time_ms:${Math.round(audio.currentTime * 1_000)}`,
            ))
            .catch(() => undefined);
        }
      },
      onPlaybackMuted: (muted) => {
        const audio = remoteAudioRef.current;
        if (!audio) return;
        audio.muted = muted;
        realtimeControllerRef.current?.recordPlaybackSignal(
          muted ? "audio_element_muted" : "audio_element_unmuted",
          `media_current_time_ms:${Math.round(audio.currentTime * 1_000)}`,
        );
      },
      onProviderEvent: (event) => {
        const eventType = typeof event.type === "string" ? event.type : "";
        if (FOLLOW_UP_ACTIVITY_EVENTS.has(eventType)) {
          wakeLifecycleRef.current?.noteActivity();
        }
        if (eventType === "conversation.item.input_audio_transcription.delta") {
          setProvisionalUser((current) => mergeProvisionalTranscript(current, {
            id: String(event.item_id ?? "live-user"),
            delta: String(event.delta ?? ""),
            afterMessageId: newestMessageIdRef.current,
          }));
        } else if (eventType === "conversation.item.input_audio_transcription.completed") {
          setProvisionalUser((current) => mergeProvisionalTranscript(current, {
            id: String(event.item_id ?? current?.id ?? "live-user"),
            transcript: String(event.transcript ?? ""),
            replace: true,
            afterMessageId: newestMessageIdRef.current,
          }));
        } else if (["response.output_audio_transcript.delta", "response.output_text.delta"].includes(eventType)) {
          setProvisionalAssistant((current) => mergeProvisionalTranscript(current, {
            id: String(event.response_id ?? event.item_id ?? "assistant-live"),
            delta: String(event.delta ?? ""),
            afterMessageId: newestMessageIdRef.current,
          }));
        } else if (["response.output_audio_transcript.done", "response.output_text.done"].includes(eventType)) {
          setProvisionalAssistant((current) => mergeProvisionalTranscript(current, {
            id: String(event.response_id ?? event.item_id ?? current?.id ?? "assistant-live"),
            transcript: String(event.transcript ?? event.text ?? ""),
            replace: true,
            afterMessageId: newestMessageIdRef.current,
          }));
        }
      },
    });
    realtimeControllerRef.current = realtimeController;
    const lifecycle = new JarvisWakeLifecycle({
      onSnapshot: setWake,
      onRearmRequested: (reason) => void rearmWakeRef.current(reason),
    });
    wakeLifecycleRef.current = lifecycle;
    const runtime = new JarvisWakeRuntime({
      onTrace: (kind, detail) => realtimeController.recordClientSignal(kind, detail),
      onReady: (stream) => {
        void realtimeController.prepare(stream).catch(() => undefined);
      },
      onSnapshot: (snapshot) => {
        if (disposed) return;
        setWakeRuntime(snapshot);
        if (snapshot.status === "ready" && lifecycle.current().status === "arming") {
          lifecycle.armed();
        } else if (snapshot.status === "failed") {
          lifecycle.fail(new Error(snapshot.error || "Wake model unavailable"));
        }
      },
      onWake: (handoff) => {
        if (disposed || lifecycle.current().status !== "armed") return;
        if (handoff.wakeProbability !== null) {
          pendingWakeAddressRef.current = {
            wakeScore: handoff.wakeProbability,
            detectedAt: Date.now(),
          };
        }
        lifecycle.detected();
        lifecycle.connecting();
        void realtimeController.start({
          audioHandoff: handoff,
          activationContext: handoff.wakeProbability === null
            ? undefined
            : { source: "wake_word", wake_score: handoff.wakeProbability },
        })
          .then(() => {
            if (!disposed) lifecycle.connected();
          })
          .catch((cause) => {
            if (disposed) return;
            pendingWakeAddressRef.current = null;
            lifecycle.fail(cause);
            setError(cause instanceof Error ? cause.message : "Unable to connect after wake");
          });
      },
    });
    wakeRuntimeRef.current = runtime;
    rearmWakeRef.current = async () => {
      try {
        await realtimeController.stop();
        await runtime.rearm();
        if (!disposed && lifecycle.current().status === "arming") lifecycle.armed();
      } catch (cause) {
        if (!disposed) lifecycle.fail(cause);
      }
    };
    void Promise.all([
      refresh(),
      getJarvisPreferences().then(setPreferences),
    ]).catch(() => undefined);
    const timer = window.setInterval(() => void refresh(), 1500);
    return () => {
      disposed = true;
      window.clearInterval(timer);
      lifecycle.destroy();
      void realtimeController.stop().finally(() => runtime.stop());
      realtimeControllerRef.current = null;
      wakeLifecycleRef.current = null;
      wakeRuntimeRef.current = null;
    };
  }, [refresh]);

  useEffect(() => {
    if (!liveCursorReady) return;
    let disposed = false;
    let source: EventSource | null = null;
    let retry: number | null = null;
    const connect = () => {
      if (disposed) return;
      source = new EventSource(jarvisLiveUrl(liveCursorRef.current), { withCredentials: true });
      source.onopen = () => {
        realtimeControllerRef.current?.recordLiveStreamState(
          "opened",
          liveCursorRef.current,
          source?.readyState,
        );
      };
      source.addEventListener("jarvis", (raw) => {
        const event = JSON.parse((raw as MessageEvent<string>).data) as JarvisLiveEvent;
        realtimeControllerRef.current?.recordBackendEvent(event);
        liveCursorRef.current = Math.max(liveCursorRef.current, event.sequence);
        if (event.event_type === "provider.user_transcript_delta") {
          setProvisionalUser((current) => mergeProvisionalTranscript(current, {
            id: String(event.payload?.item_id ?? "live-user"),
            transcript: String(event.payload?.transcript ?? ""),
            afterMessageId: newestMessageIdRef.current,
          }));
          return;
        }
        if (event.event_type === "provider.user_transcript" && pendingWakeAddressRef.current) {
          const pending = pendingWakeAddressRef.current;
          pendingWakeAddressRef.current = null;
          const decision = classifyJarvisAddress({
            transcript: String(event.payload?.transcript ?? ""),
            wakeScore: pending.wakeScore,
            speechStartedMsBeforeWake: 600,
            speechContinuedMsAfterWake: Math.max(0, Date.now() - pending.detectedAt),
            followUpWindowOpen: false,
          });
          if (!decision.accepted) {
            const lifecycle = wakeLifecycleRef.current;
            if (lifecycle && lifecycle.current().status !== "destroyed") lifecycle.sleep();
            setProvisionalUser(null);
            return;
          }
        }
        if (event.event_type === "provider.user_transcript") {
          const itemId = String(event.payload?.item_id ?? "live-user");
          setProvisionalUser((current) => mergeProvisionalTranscript(current, {
            id: itemId,
            transcript: String(event.payload?.transcript ?? ""),
            replace: true,
            afterMessageId: newestMessageIdRef.current,
          }));
          void refresh().finally(() => setProvisionalUser((current) => (
            current?.id === itemId ? null : current
          )));
          return;
        }
        if (event.event_type === "provider.assistant_transcript_delta") {
          const responseId = String(event.payload?.response_id ?? "assistant-live");
          setProvisionalAssistant((current) => mergeProvisionalTranscript(current, {
            id: responseId,
            transcript: String(event.payload?.transcript ?? ""),
            afterMessageId: newestMessageIdRef.current,
          }));
          return;
        }
        if (["provider.assistant_response", "provider.assistant_interrupted"].includes(event.event_type)) {
          const responseId = String(event.payload?.response_id ?? "");
          void refresh().finally(() => setProvisionalAssistant((current) => (
            !responseId || current?.id === responseId ? null : current
          )));
          return;
        }
        if (event.event_type === "session.rotation_requested") {
          void realtimeControllerRef.current?.stop().then(() => realtimeControllerRef.current?.start());
        }
        if (event.source_domain !== "session" || event.event_type.startsWith("task.")) {
          void refresh();
        }
      });
      source.onerror = () => {
        realtimeControllerRef.current?.recordLiveStreamState(
          "errored",
          liveCursorRef.current,
          source?.readyState,
        );
        source?.close();
        if (!disposed) retry = window.setTimeout(connect, 1000);
      };
    };
    connect();
    return () => {
      disposed = true;
      source?.close();
      if (retry !== null) window.clearTimeout(retry);
    };
  }, [refresh, liveCursorReady]);

  useEffect(() => {
    if (!WAKE_MODEL_CONFIGURATION || !currentConversationId) return;
    const runtime = wakeRuntimeRef.current;
    const lifecycle = wakeLifecycleRef.current;
    if (!runtime || !lifecycle || runtime.current().status !== "idle") return;
    lifecycle.beginArming();
    void runtime.start(WAKE_MODEL_CONFIGURATION);
  }, [currentConversationId]);

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
      wakeLifecycleRef.current?.noteActivity();
      setDraft("");
    });
  };

  const downloadRealtimeTrace = () => {
    const controller = realtimeControllerRef.current;
    const entries = controller?.traceSnapshot() ?? [];
    const url = URL.createObjectURL(new Blob(
      [JSON.stringify({
        schema: "jarvis.realtime_trace.v2",
        captured_at: new Date().toISOString(),
        browser: navigator.userAgent,
        timing_summary: controller?.timingSummary() ?? null,
        entries,
      }, null, 2)],
      { type: "application/json" },
    ));
    const anchor = document.createElement("a");
    anchor.href = url;
    anchor.download = `jarvis-realtime-trace-${Date.now()}.json`;
    anchor.click();
    URL.revokeObjectURL(url);
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

  const currentLog = state?.current_log ?? null;
  const session = state?.session ?? null;
  const sessionReady = session?.lifecycle_state === "ready";
  const ownsController = session?.controller_id === controllerIdRef.current || session?.controller_id === "typed-bridge";
  const realtimeBusy = realtime.status === "connecting" || realtime.status === "reconnecting";
  const voiceActive = realtime.status !== "idle";
  const degradedComponents = Object.entries(state?.component_health ?? {}).filter(
    ([name, component]) => name !== "worker" && !["healthy", "online"].includes(component.status),
  );
  const componentName = (name: string) => name.replaceAll("_", " ");
  const degradedNames = degradedComponents
    .slice(0, 2)
    .map(([name]) => componentName(name))
    .join(", ");

  const armVoice = async () => {
    if (!currentLog) throw new Error("Send the first turn before arming Jarvis");
    if (!realtimeControllerRef.current) throw new Error("Realtime controller is unavailable");
    const runtime = wakeRuntimeRef.current;
    const lifecycle = wakeLifecycleRef.current;
    if (runtime?.current().status === "ready" && lifecycle?.current().status === "armed") {
      lifecycle.detected();
      lifecycle.connecting();
      try {
        await realtimeControllerRef.current.start({
          audioHandoff: runtime.manualHandoff(),
        });
        lifecycle.connected();
      } catch (cause) {
        lifecycle.fail(cause);
        throw cause;
      }
    } else {
      await runtime?.stop();
      lifecycle?.reset();
      await realtimeControllerRef.current.start();
    }
  };

  const sleepVoice = async () => {
    if (
      WAKE_MODEL_CONFIGURATION
      && wakeRuntimeRef.current
      && wakeRuntimeRef.current.current().status === "triggered"
      && wakeLifecycleRef.current
    ) {
      wakeLifecycleRef.current.sleep();
    } else if (realtime.status !== "idle") {
      await realtimeControllerRef.current?.stop();
    } else if (session) {
      await closeJarvisRealtimeSession(session.id, session.generation);
    }
  };

  const toggleVoice = async () => {
    if (voiceActive) await sleepVoice();
    else await armVoice();
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
        onPlaying={(event) => realtimeControllerRef.current?.recordPlaybackSignal(
          "audio_element_playing",
          `media_current_time_ms:${Math.round(event.currentTarget.currentTime * 1_000)}`,
        )}
        onPause={(event) => realtimeControllerRef.current?.recordPlaybackSignal(
          "audio_element_paused",
          `media_current_time_ms:${Math.round(event.currentTarget.currentTime * 1_000)}`,
        )}
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
              <span className={`h-2.5 w-2.5 rounded-full ${sessionReady || realtime.status === "connected" ? "bg-emerald-400" : "bg-zinc-600"}`} />
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
              <div><span className="text-zinc-500">Voice</span><p className="mt-1 capitalize text-zinc-200">{preferences?.voice ?? "marin"}</p></div>
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
              {!!degradedComponents.length && (
                <span className="text-xs text-amber-300" title={degradedComponents.map(([name, value]) => `${name}: ${value.detail || value.status}`).join("\n")}>
                  {degradedNames}{degradedComponents.length > 2 ? ` +${degradedComponents.length - 2}` : ""}
                </span>
              )}
              {realtime.status !== "idle" && (
                <span className={realtime.status === "connected" ? "text-xs text-emerald-400" : realtime.status === "text_fallback" ? "text-xs text-amber-300" : "text-xs text-violet-300"}>
                  voice {realtime.status.replaceAll("_", " ")}{realtimeBusy ? ` · attempt ${realtime.attempt}` : ""}
                </span>
              )}
              <span
                title={wake.error || wakeRuntime.error || undefined}
                className={wake.status === "armed" ? "text-xs text-emerald-400" : wake.status === "fallback" ? "text-xs text-amber-300" : "text-xs text-zinc-500"}
              >
                {WAKE_MODEL_CONFIGURATION
                  ? `wake ${wake.status.replaceAll("_", " ")}`
                  : "wake model pending"}
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
              disabled={submitting || realtimeBusy || (!voiceActive && !currentLog)}
              aria-pressed={voiceActive}
              className={`inline-flex items-center gap-1.5 rounded-xl border px-3 py-2 text-xs disabled:opacity-40 ${
                voiceActive
                  ? "border-zinc-700 text-zinc-300"
                  : "border-violet-500/30 bg-violet-500/10 text-violet-200"
              }`}
            >
              {voiceActive ? <MicOff size={13} /> : <Mic size={13} />}
              {voiceActive ? "Sleep" : "Arm"}
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
              {provisionalUser?.text && !provisionalUserPersisted && (
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
              {provisionalAssistant?.text && !provisionalAssistantPersisted && (
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
          <div className="shrink-0 border-t border-zinc-800 bg-zinc-900 p-3 pb-[max(.75rem,env(safe-area-inset-bottom))]">
            <div className="mx-auto flex max-w-3xl items-end gap-2">
              <button
                type="button"
                onClick={() => void run(toggleVoice)}
                disabled={submitting || realtimeBusy || (!voiceActive && !currentLog)}
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
                <p className="text-xs font-medium text-amber-200">{state.pending_presentations.length} update{state.pending_presentations.length === 1 ? "" : "s"} waiting for a safe boundary</p>
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

            {preferences?.developer_mode && <div className="mt-4 rounded-2xl border border-dashed border-violet-500/30 bg-violet-500/5">
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
                  <LabButton label="Supersede" disabled={!currentLog || submitting} onClick={() => void run(async () => { controllerIdRef.current = `jarvis-browser-${crypto.randomUUID()}`; await createJarvisRealtimeCall(controllerIdRef.current, "controller"); })} />
                  <LabButton label="Provider error" disabled={!sessionReady || submitting} onClick={() => void run(() => inject("provider_error", { reason: "simulated_failure" }))} />
                  <LabButton label="Download voice trace" disabled={realtime.status === "idle"} onClick={downloadRealtimeTrace} />
                </div>
              )}
            </div>}
          </div>
        </section>
      </main>
      {mobilePanel && (
        <div className="fixed inset-0 z-40 bg-black/60 xl:hidden" onClick={() => setMobilePanel(null)}>
          <section
            role="dialog"
            aria-label={mobilePanel === "tasks" ? "Jarvis tasks" : "Jarvis activity"}
            className="absolute inset-x-0 bottom-[calc(5.25rem+env(safe-area-inset-bottom))] max-h-[70dvh] overflow-hidden rounded-t-3xl border-t border-zinc-700 bg-zinc-950 shadow-2xl"
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
          <button type="button" disabled={disabled} onClick={() => onAction(() => cancelTask(task.id))} className="min-h-12 rounded-xl px-3 text-[11px] text-rose-300 disabled:opacity-40">Cancel</button>
        )}
        {["failed", "interrupted", "cancelled"].includes(task.status) && (
          <button type="button" disabled={disabled} onClick={() => onAction(() => retryTask(task.id))} className="min-h-12 rounded-xl px-3 text-[11px] text-sky-300 disabled:opacity-40">Retry</button>
        )}
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

function LabButton({ label, disabled, onClick }: { label: string; disabled: boolean; onClick: () => void }) {
  return (
    <button type="button" disabled={disabled} onClick={onClick} className="rounded-lg border border-zinc-700 bg-zinc-900 px-2 py-2 text-[11px] text-zinc-300 hover:border-violet-500/40 disabled:opacity-40">
      {label}
    </button>
  );
}
