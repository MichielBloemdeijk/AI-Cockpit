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
  closeJarvisLiveSession,
  createJarvisLiveCall,
  getJarvisLogEvents,
  getJarvisLiveEvents,
  getJarvisPreferences,
  getJarvisSessionEvents,
  getJarvisState,
  getTaskEvents,
  jarvisLiveUrl,
  injectFakeJarvisEvent,
  retryTask,
  startFreshJarvisLog,
  submitJarvisLiveTelemetry,
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
import { JarvisLiveController } from "@/lib/jarvis-live";
import { JarvisPhoneVoiceController, phoneVoiceBridge, type PhoneAudioState } from "@/lib/jarvis-phone-voice";
import type { JarvisVoiceSnapshot } from "@/lib/jarvis-voice-client";
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
import {
  resolveJarvisWakeGateMode,
  loadJarvisWakeGateMode,
  saveJarvisWakeGateMode,
  type JarvisWakeGateMode,
} from "@/lib/jarvis-benchmark-preferences";
import { useConversationAutoScroll } from "@/lib/conversation-auto-scroll";
import {
  mergeProvisionalTranscript,
  provisionalTranscriptIsPersisted,
  type ProvisionalTranscript,
} from "@/lib/jarvis-transcript-state";
import { ACTIVE_TASK_STATUSES, taskStatusLabel } from "@/lib/task-status";
import { createUuid } from "@/lib/uuid";


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
  const [wakeGateMode, setWakeGateMode] = useState<JarvisWakeGateMode>("transcript_rules");
  const [wakeGatePreferencesLoaded, setWakeGatePreferencesLoaded] = useState(false);
  const [wakeGateBackendMode, setWakeGateBackendMode] = useState<JarvisWakeGateMode | null>(null);
  const [wakeGateMismatch, setWakeGateMismatch] = useState<string | null>(null);
  const [statusOpen, setStatusOpen] = useState(false);
  const [mobilePanel, setMobilePanel] = useState<"tasks" | "activity" | null>(null);
  const [preferences, setPreferences] = useState<JarvisPreferences | null>(null);
  const [provisionalUser, setProvisionalUser] = useState<ProvisionalTranscript | null>(null);
  const [provisionalAssistant, setProvisionalAssistant] = useState<ProvisionalTranscript | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [voice, setVoice] = useState<JarvisVoiceSnapshot>({
    status: "idle",
    micEnabled: false,
    playbackActive: false,
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
  const liveControllerRef = useRef<JarvisLiveController | JarvisPhoneVoiceController | null>(null);
  const wakeLifecycleRef = useRef<JarvisWakeLifecycle | null>(null);
  const wakeRuntimeRef = useRef<JarvisWakeRuntime | null>(null);
  const [phoneAudio, setPhoneAudio] = useState<PhoneAudioState | null>(null);
  const wakeGateModeRef = useRef<JarvisWakeGateMode>("transcript_rules");
  const developerModeRef = useRef(false);
  const refreshInFlightRef = useRef(false);
  const rearmWakeRef = useRef<(reason: "idle_timeout" | "explicit_sleep") => Promise<void>>(
    async () => undefined,
  );
  const remoteAudioRef = useRef<HTMLAudioElement | null>(null);
  const conversationScrollRef = useRef<HTMLDivElement | null>(null);
  const liveCursorRef = useRef(0);
  const liveCursorSeededRef = useRef(false);
  const newestMessageIdRef = useRef<string | null>(null);
  const liveDiagnosticSequenceRef = useRef(0);
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

  useEffect(() => {
    const developerMode = preferences?.developer_mode === true;
    developerModeRef.current = developerMode;
  }, [preferences?.developer_mode]);

  useEffect(() => {
    wakeGateModeRef.current = wakeGateMode;
  }, [wakeGateMode]);

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
    const phoneBridge = phoneVoiceBridge();
    if (phoneBridge) {
      const controller = new JarvisPhoneVoiceController(phoneBridge, setVoice, snapshot => {
        setPhoneAudio(snapshot);
        setWake({ status: snapshot.mode === "waiting" ? "armed" : snapshot.mode === "wake_starting" ? "arming" : "idle",
          error: snapshot.mode === "error" ? snapshot.text : null, followUpUntil: null });
      });
      liveControllerRef.current = controller;
      void refresh();
      void getJarvisPreferences().then(next => { if (!disposed) { setPreferences(next); setWakeGatePreferencesLoaded(true); } })
        .catch(cause => { if (!disposed) setError(cause instanceof Error ? cause.message : "Unable to load Jarvis preferences"); });
      const timer = window.setInterval(() => void refresh(), 1500);
      return () => { disposed = true; window.clearInterval(timer); controller.dispose(); liveControllerRef.current = null; };
    }
    controllerIdRef.current = `jarvis-browser-${createUuid()}`;
    const handleProviderEvent = (event: Record<string, unknown>) => {
      const eventType = typeof event.type === "string" ? event.type : "";
      if (FOLLOW_UP_ACTIVITY_EVENTS.has(eventType) || eventType.startsWith("session.")) {
        wakeLifecycleRef.current?.noteActivity();
      }
      if (["conversation.item.input_audio_transcription.delta", "session.input_transcript.delta"].includes(eventType)) {
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
      } else if (["response.output_audio_transcript.delta", "response.output_text.delta", "session.output_transcript.delta"].includes(eventType)) {
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
    };
    const handleRemoteStream = (stream: MediaStream | null) => {
      const audio = remoteAudioRef.current;
      if (!audio) return;
      audio.srcObject = stream;
      if (stream) {
        audio.muted = false;
        void audio.play().catch(() => undefined);
      }
    };
    const handleLiveSnapshot = (snapshot: JarvisVoiceSnapshot) => {
      if (disposed) return;
      setVoice(snapshot);
    };
    const liveController = new JarvisLiveController(
      controllerIdRef.current,
      {
        onSnapshot: handleLiveSnapshot,
        onRemoteStream: handleRemoteStream,
        playbackElement: () => remoteAudioRef.current,
        onProviderEvent: handleProviderEvent,
        onDiagnostic: (diagnostic) => {
          const traceId = diagnostic.activationId
            ?? diagnostic.sessionId
            ?? `live-${controllerIdRef.current.slice(-36)}-${diagnostic.attempt}`;
          void submitJarvisLiveTelemetry([{
            trace_id: traceId,
            client_sequence: ++liveDiagnosticSequenceRef.current,
            activation_id: diagnostic.activationId,
            session_id: diagnostic.sessionId,
            generation: diagnostic.generation,
            source: "browser",
            event_type: `live.${diagnostic.event}`,
            observed_at: diagnostic.observedAt,
            monotonic_ms: performance.now(),
            detail: { attempt: diagnostic.attempt, ...diagnostic.detail },
          }]).catch(() => undefined);
        },
      },
    );
    liveControllerRef.current = liveController;
    const handlePageHide = () => { void liveController.stop(); };
    window.addEventListener("pagehide", handlePageHide);
    const lifecycle = new JarvisWakeLifecycle({
      onSnapshot: setWake,
      onRearmRequested: (reason) => void rearmWakeRef.current(reason),
    });
    wakeLifecycleRef.current = lifecycle;
    const runtime = new JarvisWakeRuntime({
      onTrace: (event, detail) => {
        if (!["wake_runtime_starting", "wake_microphone_ready", "wake_runtime_failed"].includes(event)) return;
        void submitJarvisLiveTelemetry([{
          trace_id: `wake-${controllerIdRef.current.slice(-36)}`,
          client_sequence: ++liveDiagnosticSequenceRef.current,
          source: "browser", event_type: event, observed_at: new Date().toISOString(),
          monotonic_ms: performance.now(), detail,
        }]).catch(() => undefined);
      },
      onReady: (stream) => {
        void liveController.prepare(stream).catch((cause) => {
          if (disposed) return;
          wakeLifecycleRef.current?.fail(cause);
          setError(cause instanceof Error ? cause.message : "Unable to prepare GPT-Live transport");
        });
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
        lifecycle.detected();
        lifecycle.connecting();
        const activationId = handoff.activationId ?? createUuid();
        setWakeGateBackendMode(null);
        setWakeGateMismatch(null);
        const effectiveGateMode = resolveJarvisWakeGateMode(
          developerModeRef.current,
          wakeGateModeRef.current,
        );
        const activationContext = handoff.wakeProbability === null
          ? undefined
          : {
                source: "wake_word",
                wake_score: handoff.wakeProbability,
                activation_id: activationId,
                gate_mode: effectiveGateMode,
              } as const;
        const liveCandidateAudio = handoff.candidateAudio && "observeEndpoint" in handoff.candidateAudio
          ? handoff.candidateAudio
          : undefined;
        const connection = liveController.startWithOptions({
          stream: handoff.localStream,
          activationContext,
          candidateAudio: liveCandidateAudio,
          wakeDetectedMonotonicMs: handoff.wakeDetectedMonotonicMs,
        });
        void connection
          .then((snapshot) => {
            if (!disposed && snapshot.status === "connected") lifecycle.connected();
          })
          .catch((cause) => {
            if (disposed) return;
            lifecycle.fail(cause);
            setError(cause instanceof Error ? cause.message : "Unable to connect after wake");
          });
      },
    });
    wakeRuntimeRef.current = runtime;
    const refreshLivePreparation = (reason: "microphone_change" | "network_change") => {
      void liveController.refreshPreparation(reason).catch((cause) => {
        if (!disposed) setError(cause instanceof Error ? cause.message : "Unable to refresh GPT-Live transport");
      });
    };
    const handleDeviceChange = () => {
      if (
        !WAKE_MODEL_CONFIGURATION
        || runtime.current().status !== "ready"
        || !["idle", "closed"].includes(liveController.current().status)
      ) return;
      void runtime.stop()
        .then(() => runtime.start(WAKE_MODEL_CONFIGURATION))
        .catch((cause) => {
          if (!disposed) setError(cause instanceof Error ? cause.message : "Unable to refresh the microphone");
        });
    };
    const handleNetworkChange = () => refreshLivePreparation("network_change");
    navigator.mediaDevices?.addEventListener?.("devicechange", handleDeviceChange);
    window.addEventListener("online", handleNetworkChange);
    const networkInformation = (navigator as Navigator & {
      connection?: { addEventListener?: (type: string, listener: () => void) => void; removeEventListener?: (type: string, listener: () => void) => void };
    }).connection;
    networkInformation?.addEventListener?.("change", handleNetworkChange);
    rearmWakeRef.current = async () => {
      try {
        if (!["idle", "closed"].includes(liveController.current().status)) {
          await liveController.stop();
        }
        await runtime.rearm();
        if (!disposed && lifecycle.current().status === "arming") lifecycle.armed();
      } catch (cause) {
        if (!disposed) lifecycle.fail(cause);
      }
    };
    void Promise.all([
      refresh(),
      getJarvisPreferences().then((nextPreferences) => {
        setPreferences(nextPreferences);
        const selected = loadJarvisWakeGateMode();
        wakeGateModeRef.current = selected;
        setWakeGateMode(selected);
        setWakeGatePreferencesLoaded(true);
      }),
    ]).catch(() => undefined);
    const timer = window.setInterval(() => void refresh(), 1500);
    return () => {
      disposed = true;
      window.clearInterval(timer);
      window.removeEventListener("pagehide", handlePageHide);
      navigator.mediaDevices?.removeEventListener?.("devicechange", handleDeviceChange);
      window.removeEventListener("online", handleNetworkChange);
      networkInformation?.removeEventListener?.("change", handleNetworkChange);
      lifecycle.destroy();
      void liveController.stop().finally(() => runtime.stop());
      liveControllerRef.current = null;
      wakeLifecycleRef.current = null;
      wakeRuntimeRef.current = null;
    };
  }, [refresh]);

  useEffect(() => {
    if (!liveCursorReady) return;
    let disposed = false;
    let source: EventSource | null = null;
    let retry: number | null = null;
    let polling = false;

    const deliver = (event: JarvisLiveEvent) => {
      if (event.sequence <= liveCursorRef.current) return;
      liveControllerRef.current?.recordBackendEvent(event);
      liveCursorRef.current = event.sequence;
      if (event.event_type === "provider.user_transcript_delta") {
        setProvisionalUser((current) => mergeProvisionalTranscript(current, {
          id: String(event.payload?.item_id ?? "live-user"),
          delta: String(event.payload?.delta ?? event.payload?.transcript ?? ""),
          afterMessageId: newestMessageIdRef.current,
        }));
        return;
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
      if (event.source_domain !== "session" || event.event_type.startsWith("task.")) {
        void refresh();
      }
    };

    const catchUp = () => {
      if (disposed || polling) return;
      polling = true;
      void getJarvisLiveEvents(liveCursorRef.current)
        .then((response) => response.events
          .sort((left, right) => left.sequence - right.sequence)
          .forEach(deliver))
        .catch(() => undefined)
        .finally(() => { polling = false; });
    };

    const connect = () => {
      if (disposed) return;
      source = new EventSource(jarvisLiveUrl(liveCursorRef.current), { withCredentials: true });
      source.onopen = () => {
        liveControllerRef.current?.recordLiveStreamState("opened");
      };
      source.addEventListener("jarvis", (raw) => {
        try {
          deliver(JSON.parse((raw as MessageEvent<string>).data) as JarvisLiveEvent);
        } catch {
          // Durable replay recovers malformed or incomplete stream frames.
        }
      });
      source.onerror = () => {
        liveControllerRef.current?.recordLiveStreamState("errored");
        source?.close();
        if (!disposed) retry = window.setTimeout(connect, 1000);
      };
    };
    connect();
    catchUp();
    const pollTimer = window.setInterval(catchUp, 1000);
    return () => {
      disposed = true;
      source?.close();
      if (retry !== null) window.clearTimeout(retry);
      window.clearInterval(pollTimer);
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
      await acceptJarvisTextTurn(content, createUuid());
      wakeLifecycleRef.current?.noteActivity();
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
      event_id: `lab-${createUuid()}`,
      kind,
      payload,
    });
  };

  const currentLog = state?.current_log ?? null;
  const session = state?.session ?? null;
  const sessionReady = session?.lifecycle_state === "ready";
  const ownsController = liveControllerRef.current?.ownsSession(session)
    || session?.controller_id === controllerIdRef.current || session?.controller_id === "typed-bridge";
  const voiceBusy = voice.status === "connecting";
  const voiceActive = !["idle", "closed", "error"].includes(voice.status);
  const effectiveWakeGateMode = resolveJarvisWakeGateMode(
    preferences?.developer_mode === true,
    wakeGateMode,
  );
  const degradedComponents = Object.entries(state?.component_health ?? {}).filter(
    ([name, component]) => name !== "worker" && !["healthy", "online"].includes(component.status),
  );
  const componentName = (name: string) => name.replaceAll("_", " ");
  const degradedNames = degradedComponents
    .slice(0, 2)
    .map(([name]) => componentName(name))
    .join(", ");

  const armVoice = async () => {
    if (liveControllerRef.current instanceof JarvisPhoneVoiceController) {
      await liveControllerRef.current.startWithOptions(); return;
    }
    if (!currentLog) throw new Error("Send the first turn before arming Jarvis");
    if (!liveControllerRef.current) throw new Error("Voice controller is unavailable");
    const runtime = wakeRuntimeRef.current;
    const lifecycle = wakeLifecycleRef.current;
    if (runtime?.current().status === "ready" && lifecycle?.current().status === "armed") {
      lifecycle.detected();
      lifecycle.connecting();
      try {
        const handoff = runtime.manualHandoff();
        await liveControllerRef.current.startWithOptions({ stream: handoff.localStream });
        lifecycle.connected();
      } catch (cause) {
        lifecycle.fail(cause);
        throw cause;
      }
    } else {
      await runtime?.stop();
      lifecycle?.reset();
      await liveControllerRef.current.startWithOptions();
    }
  };

  const sleepVoice = async () => {
    if (liveControllerRef.current instanceof JarvisPhoneVoiceController) {
      await liveControllerRef.current.stop(); return;
    }
    if (
      WAKE_MODEL_CONFIGURATION
      && wakeRuntimeRef.current
      && wakeRuntimeRef.current.current().status === "triggered"
      && wakeLifecycleRef.current
    ) {
      wakeLifecycleRef.current.sleep();
    } else if (voice.status !== "idle") {
      await liveControllerRef.current?.stop();
    } else if (session) {
      await closeJarvisLiveSession(session.id, session.generation);
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
              <span className={`h-2.5 w-2.5 rounded-full ${sessionReady || voice.status === "connected" ? "bg-emerald-400" : "bg-zinc-600"}`} />
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
              {voice.status !== "idle" && (
                <span className={voice.status === "connected" ? "text-xs text-emerald-400" : voice.status === "error" ? "text-xs text-amber-300" : "text-xs text-violet-300"}>
                  voice {voice.status.replaceAll("_", " ")}
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
              <span className="text-[11px] text-zinc-500">
                gate {wakeGatePreferencesLoaded
                  ? `${wakeGateMode} → ${effectiveWakeGateMode}${wakeGateBackendMode ? ` → ${wakeGateBackendMode}` : ""}`
                  : "initializing"}
              </span>
              {wakeGateMismatch && (
                <span className="text-[11px] text-amber-300" title={wakeGateMismatch}>
                  gate mismatch
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
              <button type="button" disabled={submitting} onClick={() => void run(async () => {
                const controller = liveControllerRef.current;
                if (controller instanceof JarvisPhoneVoiceController) await controller.wake();
              })} className="rounded-xl border border-zinc-700 px-3 py-2 text-xs text-zinc-300 disabled:opacity-40">Hey Jarvis</button>
            )}
            {phoneAudio && <span className="text-xs text-zinc-400">{phoneAudio.text}</span>}
            <Link href="/chat" className="inline-flex items-center gap-1.5 rounded-xl border border-zinc-700 px-3 py-2 text-xs text-zinc-300">
              <History size={13} /> History
            </Link>
            <button
              type="button"
              onClick={() => void run(async () => {
                await startFreshJarvisLog(createUuid());
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

      {preferences?.developer_mode && <div className="shrink-0 border-b border-violet-500/20 bg-zinc-950 px-3 py-2 md:px-6">
        <div className="mx-auto max-w-3xl rounded-2xl border border-dashed border-violet-500/30 bg-violet-500/5">
          <button type="button" onClick={() => setLabOpen((value) => !value)} className="flex min-h-12 w-full items-center justify-between px-3 py-2 text-left text-xs font-medium text-violet-200">
            <span className="flex items-center gap-2"><FlaskConical size={14} /> Developer conversation lab</span>
            <span>{labOpen ? "Hide" : "Show"}</span>
          </button>
          {labOpen && (
            <div className="grid grid-cols-2 gap-2 border-t border-violet-500/20 p-3">
              <label className="col-span-2 rounded-xl border border-violet-500/20 bg-zinc-950/40 p-2 text-xs text-violet-100">
                <span className="block font-medium">Wake gate benchmark</span>
                <select
                  value={wakeGateMode}
                  disabled={!wakeGatePreferencesLoaded || !preferences}
                  onChange={(event) => {
                    const mode = event.target.value as JarvisWakeGateMode;
                    wakeGateModeRef.current = mode;
                    setWakeGateMode(mode);
                    saveJarvisWakeGateMode(mode);
                  }}
                  className="mt-2 min-h-10 w-full rounded-lg border border-zinc-700 bg-zinc-900 px-2 text-zinc-200"
                >
                  <option value="transcript_rules">Transcript rules (default)</option>
                  <option value="immediate" disabled={!preferences?.developer_mode}>Immediate (developer)</option>
                </select>
                <span className="mt-1 block text-[11px] text-zinc-500">
                  Selected: {wakeGateMode}; effective: {effectiveWakeGateMode}. Immediate mode is available only while developer mode is enabled.
                </span>
              </label>
              <LabButton label="User speaking" disabled={!sessionReady || submitting} onClick={() => void run(() => inject("user_speaking"))} />
              <LabButton label="Release floor" disabled={!sessionReady || submitting} onClick={() => void run(() => inject("user_stopped"))} />
              <LabButton label="Direct reply" disabled={!sessionReady || submitting} onClick={() => void run(() => inject("assistant_response", { response_id: `lab-${createUuid()}`, item_id: `lab-${createUuid()}`, transcript: "This is a deterministic direct response." }))} />
              <LabButton label="Interrupt reply" disabled={!sessionReady || submitting} onClick={() => void run(() => inject("assistant_interrupted", { response_id: `lab-${createUuid()}`, item_id: `lab-${createUuid()}`, transcript: "This response was interrupted before it finished.", heard_transcript: "This response was interrupted" }))} />
              <LabButton label="Delegate demo" disabled={!sessionReady || submitting} onClick={() => void run(() => inject("shell_call", { call_id: `lab-${createUuid()}`, name: "dispatch_task", arguments: { goal: "Prepare the developer lab report" } }))} />
              <LabButton label="Supersede" disabled={!currentLog || submitting} onClick={() => void run(async () => { controllerIdRef.current = `jarvis-browser-${createUuid()}`; await createJarvisLiveCall(controllerIdRef.current, "controller"); })} />
              <LabButton label="Provider error" disabled={!sessionReady || submitting} onClick={() => void run(() => inject("provider_error", { reason: "simulated_failure" }))} />
            </div>
          )}
        </div>
      </div>}
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
