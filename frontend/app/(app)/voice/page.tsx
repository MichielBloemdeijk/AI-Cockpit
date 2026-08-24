"use client";

import { FormEvent, useCallback, useEffect, useRef, useState } from "react";
import { Bell, Download, Loader2, Mic, MicOff, RotateCcw, Send, Volume2, VolumeX, Wifi, X } from "lucide-react";
import {
  acknowledgePushDelivery,
  answerVoiceQuestion,
  cancelVoiceTask,
  getVoiceHealth,
  getVoiceState,
  getVoiceUpdate,
  getVapidPublicKey,
  resetVoiceConversation,
  savePushSubscription,
  startVoiceTurn,
  streamConversationEvents,
  synthesizeVoice,
  transcribeVoiceSegment,
  type ConversationEventView,
  type VoiceHealthResponse,
  type VoicePendingQuestion,
  type VoiceTaskView,
} from "@/lib/api";
import { JarvisSpeechScheduler, type SpeechPlayback } from "@/lib/jarvis-speech-scheduler";
import { isVoiceSpeechEvent, isVoiceTerminalEvent } from "@/lib/jarvis-event-policy";
import { JarvisTurnGate } from "@/lib/jarvis-turn-gate";
import { JarvisVoiceStateMachine, type VoicePhase } from "@/lib/jarvis-voice-state";
import { restartJarvisVad, type JarvisVadInstance } from "@/lib/jarvis-vad";

type InstallPrompt = Event & { prompt: () => Promise<void> };
const CURSOR_PREFIX = "ai-cockpit-voice-cursor:";

function encodeWav(audio: Float32Array): Blob {
  const buffer = new ArrayBuffer(44 + audio.length * 2);
  const view = new DataView(buffer);
  const write = (offset: number, value: string) => value.split("").forEach((char, index) => view.setUint8(offset + index, char.charCodeAt(0)));
  write(0, "RIFF"); view.setUint32(4, 36 + audio.length * 2, true); write(8, "WAVE"); write(12, "fmt ");
  view.setUint32(16, 16, true); view.setUint16(20, 1, true); view.setUint16(22, 1, true); view.setUint32(24, 16000, true);
  view.setUint32(28, 32000, true); view.setUint16(32, 2, true); view.setUint16(34, 16, true); write(36, "data"); view.setUint32(40, audio.length * 2, true);
  audio.forEach((value, index) => { const sample = Math.max(-1, Math.min(1, value || 0)); view.setInt16(44 + index * 2, sample < 0 ? sample * 32768 : sample * 32767, true); });
  return new Blob([buffer], { type: "audio/wav" });
}

function playBrowserAudio(blob: Blob): SpeechPlayback {
  const url = URL.createObjectURL(blob);
  const audio = new Audio(url);
  let rejectPlayback: ((reason?: unknown) => void) | null = null;
  let settled = false;
  const finished = new Promise<void>((resolve, reject) => {
    rejectPlayback = reject;
    audio.onended = () => { settled = true; resolve(); };
    audio.onerror = () => { settled = true; reject(new Error("Audio playback failed")); };
    void audio.play().catch((error) => { settled = true; reject(error); });
  }).finally(() => URL.revokeObjectURL(url));
  return {
    finished,
    stop: () => {
      audio.pause();
      if (!settled) { settled = true; rejectPlayback?.(new Error("Speech interrupted")); }
    },
  };
}

export default function VoicePage() {
  const machineRef = useRef<JarvisVoiceStateMachine | null>(null);
  if (!machineRef.current) machineRef.current = new JarvisVoiceStateMachine();
  const machine = machineRef.current;
  const [voiceSnapshot, setVoiceSnapshot] = useState(machine.current);
  const voiceState = voiceSnapshot.phase;
  const [health, setHealth] = useState<VoiceHealthResponse | null>(null);
  const [conversationId, setConversationId] = useState("");
  const [tasks, setTasks] = useState<VoiceTaskView[]>([]);
  const [questions, setQuestions] = useState<VoicePendingQuestion[]>([]);
  const [transcript, setTranscript] = useState("");
  const [lastReply, setLastReply] = useState("");
  const [error, setError] = useState("");
  const [textDraft, setTextDraft] = useState("");
  const [answerDraft, setAnswerDraft] = useState("");
  const [installPrompt, setInstallPrompt] = useState<InstallPrompt | null>(null);
  const [alertsEnabled, setAlertsEnabled] = useState(false);
  const vadRef = useRef<JarvisVadInstance | null>(null);
  const streamRef = useRef<AbortController | null>(null);
  const reconnectRef = useRef<number | null>(null);
  const eventChainRef = useRef<Promise<void>>(Promise.resolve());
  const connectStreamRef = useRef<(id: string, initialSequence?: number) => void>(() => undefined);
  const conversationIdRef = useRef("");
  const questionsRef = useRef<VoicePendingQuestion[]>([]);
  const schedulerRef = useRef<JarvisSpeechScheduler | null>(null);
  const turnGateRef = useRef<JarvisTurnGate | null>(null);
  if (!turnGateRef.current) turnGateRef.current = new JarvisTurnGate();
  const turnGate = turnGateRef.current;

  if (!schedulerRef.current) {
    schedulerRef.current = new JarvisSpeechScheduler(machine, {
      synthesize: async (text) => (await synthesizeVoice(text)).audio,
      play: playBrowserAudio,
      pauseVad: () => vadRef.current?.pause(),
      resumeVad: () => restartJarvisVad(vadRef.current, machine.current.micEnabled),
      acknowledgeSpoken: (deliveryId) => acknowledgePushDelivery(deliveryId, "spoken"),
      isVisible: () => document.visibilityState === "visible",
      onError: (reason) => setError(String(reason)),
    });
  }
  const scheduler = schedulerRef.current;

  const updateQuestions = useCallback((updater: (current: VoicePendingQuestion[]) => VoicePendingQuestion[]) => {
    setQuestions((current) => {
      const next = updater(current);
      questionsRef.current = next;
      return next;
    });
  }, []);

  const handleEvent = useCallback(async (event: ConversationEventView, deliveryIdOverride?: string) => {
    const payload = event.payload_json || {};
    const delivery = (payload.voice_delivery || {}) as Record<string, unknown>;
    const runId = event.run_id || "";
    if (event.event_type.startsWith("task.")) {
      setTasks((current) => {
        const existing = current.find((task) => task.run_id === runId);
        const next: VoiceTaskView = {
          run_id: runId,
          conversation_id: conversationIdRef.current,
          parent_run_id: existing?.parent_run_id || null,
          title: String(payload.title || existing?.title || "Background task"),
          goal: String(payload.goal || existing?.goal || ""),
          status: String(payload.status || existing?.status || "queued"),
          priority: String(payload.priority || existing?.priority || "normal"),
          notify_policy: String(payload.notify_policy || existing?.notify_policy || "important"),
          requested_notification: Boolean(payload.requested_notification ?? existing?.requested_notification),
          summary: String(payload.summary || existing?.summary || "") || null,
          error: event.event_type === "task.failed" ? String(payload.summary || "Task failed") : existing?.error || null,
          started_at: existing?.started_at || new Date().toISOString(),
          finished_at: ["task.completed", "task.failed", "task.cancelled", "task.interrupted"].includes(event.event_type) ? new Date().toISOString() : existing?.finished_at || null,
        };
        return existing ? current.map((task) => task.run_id === runId ? next : task) : [next, ...current];
      });
      if (isVoiceTerminalEvent(event.event_type)) {
        await scheduler.dismissQuestion(runId);
        updateQuestions((current) => current.filter((item) => item.run_id !== runId));
      }
    }
    if (event.event_type.startsWith("run.") && isVoiceTerminalEvent(event.event_type)) {
      await scheduler.dismissQuestion(runId);
      updateQuestions((current) => current.filter((item) => item.run_id !== runId));
      if (["thinking", "transcribing"].includes(machine.current.phase)) machine.setPhase(machine.current.micEnabled ? "armed" : "stopped");
    }
    const speechText = String(delivery.speech_text || payload.speech_text || payload.summary || payload.content || payload.question || "").trim();
    if (event.event_type === "conversation.assistant.message.completed") {
      if (speechText) setLastReply(speechText);
      if (["thinking", "transcribing"].includes(machine.current.phase)) machine.setPhase(machine.current.micEnabled ? "armed" : "stopped");
    }
    if (event.event_type === "agent.question.asked" || event.event_type === "task.requires_input") {
      const nextQuestion: VoicePendingQuestion = {
        run_id: runId,
        question_id: String(payload.id || ""),
        question: speechText,
        kind: String(payload.kind || "question"),
        asked_at: String(payload.asked_at || "") || null,
      };
      updateQuestions((current) => current.some((item) => item.run_id === runId)
        ? current.map((item) => item.run_id === runId ? { ...item, ...nextQuestion, question: nextQuestion.question || item.question } : item)
        : [...current, nextQuestion]);
      if (machine.current.phase === "thinking") machine.setPhase(machine.current.micEnabled ? "armed" : "stopped");
    }
    if (event.event_type === "agent.question.answered") {
      await scheduler.dismissQuestion(runId);
      updateQuestions((current) => current.filter((item) => item.run_id !== runId));
    }
    const isSpeechEvent = isVoiceSpeechEvent(event.event_type);
    if (delivery.speak === true && isSpeechEvent && speechText) {
      await scheduler.enqueue({
        eventId: event.id,
        text: speechText,
        deliveryId: deliveryIdOverride || String(payload.delivery_id || delivery.delivery_id || "") || undefined,
        urgent: String(payload.importance || delivery.importance) === "urgent",
        answerRunId: ["agent.question.asked", "task.requires_input"].includes(event.event_type) ? runId : undefined,
      });
    } else if (event.event_type === "conversation.assistant.message.completed" || isVoiceTerminalEvent(event.event_type)) {
      await scheduler.resumeListening();
    }
    const id = conversationIdRef.current;
    if (id && event.sequence > Number(window.localStorage.getItem(`${CURSOR_PREFIX}${id}`) || 0)) window.localStorage.setItem(`${CURSOR_PREFIX}${id}`, String(event.sequence));
  }, [machine, scheduler, updateQuestions]);

  const queueEvent = useCallback((event: ConversationEventView, deliveryIdOverride?: string): Promise<void> => {
    const next = eventChainRef.current.then(() => handleEvent(event, deliveryIdOverride));
    eventChainRef.current = next.catch((reason) => { setError(String(reason)); });
    return next;
  }, [handleEvent]);

  const connectStream = useCallback((id: string, initialSequence = 0) => {
    streamRef.current?.abort();
    const stored = Number(window.localStorage.getItem(`${CURSOR_PREFIX}${id}`) || "");
    const cursor = Number.isFinite(stored) && stored > 0 ? stored : initialSequence;
    streamRef.current = streamConversationEvents(id, cursor, (event) => { void queueEvent(event); }, () => {
      if (reconnectRef.current) window.clearTimeout(reconnectRef.current);
      reconnectRef.current = window.setTimeout(() => connectStreamRef.current(id), 1000);
    });
  }, [queueEvent]);
  connectStreamRef.current = connectStream;

  const submitCommand = useCallback(async (command: string, cloudNextTurn = machine.current.cloudNextTurn): Promise<boolean> => {
    const text = command.trim();
    if (!text) return false;
    machine.setCloudNextTurn(false);
    setError(""); setTranscript(text); machine.beginTurn();
    if (machine.current.micEnabled) await vadRef.current?.pause();
    try {
      await startVoiceTurn(text, cloudNextTurn);
      return true;
    } catch (reason) {
      setError(String(reason));
      machine.setPhase(machine.current.micEnabled ? "armed" : "stopped");
      await scheduler.resumeListening();
      return false;
    }
  }, [machine, scheduler]);

  const processAudio = useCallback(async (audio: Float32Array, followUpAuthorized: boolean) => {
    machine.setPhase("transcribing");
    try {
      const result = await transcribeVoiceSegment(encodeWav(audio));
      setTranscript(result.transcript);
      const route = machine.routeTranscript(result.command, result.wake_word_match, followUpAuthorized);
      if (route.kind === "acknowledge") await scheduler.speakTransient(route.text);
      else if (route.kind === "answer") {
        try {
          await answerVoiceQuestion(route.runId, route.answer);
          await scheduler.dismissQuestion(route.runId);
          updateQuestions((current) => current.filter((item) => item.run_id !== route.runId));
          machine.openFollowUp();
          await scheduler.resumeListening();
        } catch (reason) {
          machine.presentQuestion(route.runId);
          machine.openFollowUp();
          throw reason;
        }
      } else if (route.kind === "command") await submitCommand(route.command, route.cloudNextTurn);
      else {
        scheduler.onStateChanged();
        await scheduler.resumeListening();
      }
    } catch (reason) {
      setError(String(reason));
      if (machine.current.phase !== "follow-up") machine.setPhase(machine.current.micEnabled ? "armed" : "stopped");
      scheduler.onStateChanged();
      await scheduler.resumeListening();
    } finally {
      turnGate.finishProcessing();
    }
  }, [machine, scheduler, submitCommand, turnGate, updateQuestions]);

  const startVAD = useCallback(async () => {
    scheduler.retryPending();
    if (vadRef.current) {
      machine.setMicEnabled(true);
      await vadRef.current.start();
      return;
    }
    try {
      const { MicVAD } = await import("@ricky0123/vad-web");
      const vad = await MicVAD.new({
        model: "v5",
        baseAssetPath: "/vad/",
        onnxWASMBasePath: "/vad/",
        onSpeechStart: () => {
          const state = machine.current;
          const safeBoundary = turnGate.beginSegment(state);
          if (safeBoundary) machine.setPhase("listening");
          else void vadRef.current?.pause();
        },
        onSpeechEnd: (audio: Float32Array) => {
          const accepted = turnGate.beginProcessing();
          if (!accepted) return;
          void (async () => {
            await vadRef.current?.pause();
            await processAudio(audio, accepted.followUpAuthorized);
          })();
        },
      });
      vadRef.current = vad as JarvisVadInstance;
      await vad.start();
      machine.setMicEnabled(true);
      machine.setPhase("armed");
    } catch (reason) {
      vadRef.current = null;
      machine.setMicEnabled(false);
      setError(`Microphone/VAD unavailable: ${String(reason)}`);
    }
  }, [machine, processAudio, scheduler, turnGate]);

  const stopVAD = useCallback(() => {
    machine.setMicEnabled(false);
    vadRef.current?.destroy();
    vadRef.current = null;
  }, [machine]);

  const interruptSpeech = useCallback(async () => {
    await scheduler.interrupt();
    machine.openFollowUp();
  }, [machine, scheduler]);

  useEffect(() => {
    scheduler.activate();
    const unsubscribe = machine.subscribe((snapshot) => { setVoiceSnapshot(snapshot); scheduler.onStateChanged(); });
    const onVisibility = () => document.visibilityState === "visible" ? scheduler.retryPending() : scheduler.onStateChanged();
    document.addEventListener("visibilitychange", onVisibility);
    return () => {
      unsubscribe();
      document.removeEventListener("visibilitychange", onVisibility);
      machine.setMicEnabled(false);
      void scheduler.shutdown();
    };
  }, [machine, scheduler]);

  useEffect(() => {
    let cancelled = false;
    void getVoiceHealth().then((value) => { if (!cancelled) setHealth(value); }).catch((reason) => setError(String(reason)));
    void getVoiceState().then(async (value) => {
      if (cancelled) return;
      const id = value.conversation.conversation_id;
      conversationIdRef.current = id;
      questionsRef.current = value.pending_questions;
      setConversationId(id); setTasks(value.tasks); setQuestions(value.pending_questions);
      await scheduler.recover(new Set(value.pending_questions.map((question) => question.run_id)));
      if (cancelled) return;
      connectStream(id, value.latest_sequence);
    }).catch((reason) => setError(String(reason)));
    const onInstall = (event: Event) => { event.preventDefault(); setInstallPrompt(event as InstallPrompt); };
    window.addEventListener("beforeinstallprompt", onInstall);
    return () => {
      cancelled = true;
      window.removeEventListener("beforeinstallprompt", onInstall);
      streamRef.current?.abort();
      if (reconnectRef.current) window.clearTimeout(reconnectRef.current);
      vadRef.current?.destroy();
      vadRef.current = null;
    };
  }, [connectStream, machine, scheduler]);

  useEffect(() => {
    navigator.serviceWorker?.register("/sw.js", { scope: "/", updateViaCache: "none" }).catch(() => undefined);
    const onMessage = (event: MessageEvent<{ type?: string; payload?: { event_id?: string; delivery_id?: string } }>) => {
      const payload = event.data?.payload || {};
      if ((event.data?.type === "jarvis-push" || event.data?.type === "jarvis-open-update") && payload.event_id) {
        void getVoiceUpdate(payload.event_id).then((item) => queueEvent(item, payload.delivery_id));
      }
      if (payload.delivery_id) void acknowledgePushDelivery(payload.delivery_id, "opened");
    };
    navigator.serviceWorker?.addEventListener("message", onMessage);
    return () => navigator.serviceWorker?.removeEventListener("message", onMessage);
  }, [queueEvent]);

  useEffect(() => {
    const params = new URLSearchParams(window.location.search);
    const eventId = params.get("update");
    const deliveryId = params.get("delivery") || undefined;
    if (deliveryId) void acknowledgePushDelivery(deliveryId, "opened");
    if (eventId) void getVoiceUpdate(eventId).then((event) => queueEvent(event, deliveryId)).catch((reason) => setError(String(reason)));
  }, [queueEvent]);

  const enableAlerts = async () => {
    try {
      const permission = await Notification.requestPermission();
      if (permission !== "granted") throw new Error("Notifications were not enabled.");
      const key = await getVapidPublicKey();
      if (!key.enabled || !key.public_key) throw new Error("Web Push is not configured on the server.");
      const registration = await navigator.serviceWorker.ready;
      const raw = atob(key.public_key.replace(/-/g, "+").replace(/_/g, "/"));
      const applicationServerKey = Uint8Array.from(raw, (char) => char.charCodeAt(0));
      await savePushSubscription(await registration.pushManager.subscribe({ userVisibleOnly: true, applicationServerKey }));
      setAlertsEnabled(true);
    } catch (reason) { setError(String(reason)); }
  };

  const submitAnswer = async (event: FormEvent) => {
    event.preventDefault();
    const question = questionsRef.current[0];
    const answer = answerDraft.trim();
    if (!question || !answer) return;
    try {
      machine.setPhase("thinking");
      await answerVoiceQuestion(question.run_id, answer);
      await scheduler.dismissQuestion(question.run_id);
      setAnswerDraft("");
      updateQuestions((current) => current.filter((item) => item.run_id !== question.run_id));
    } catch (reason) { setError(String(reason)); }
  };

  const resetConversation = async () => {
    try {
      const conversation = await resetVoiceConversation();
      stopVAD();
      conversationIdRef.current = conversation.conversation_id;
      questionsRef.current = [];
      setConversationId(conversation.conversation_id); setTasks([]); setQuestions([]); setTranscript(""); setLastReply(""); setError("");
      connectStream(conversation.conversation_id, 0);
    } catch (reason) { setError(String(reason)); }
  };

  const statusText: Record<VoicePhase, string> = { stopped: "Microphone stopped", armed: `Listening for “${health?.wake_word || "Jarvis"}”`, listening: "Listening…", transcribing: "Transcribing locally…", thinking: "Working on it…", speaking: "Speaking…", "follow-up": "Follow-up window open" };
  const healthCards: Array<[string, Record<string, unknown> | undefined]> = [["Brain", health?.llm], ["Speech to text", health?.stt], ["Text to speech", health?.tts]];

  return <div className="flex min-h-full flex-1 flex-col bg-zinc-950 px-4 py-6 sm:px-8"><div className="mx-auto flex w-full max-w-3xl flex-1 flex-col gap-5"><header className="flex items-center justify-between"><div><p className="text-xs uppercase tracking-[0.24em] text-cyan-400">AI Cockpit</p><h1 className="mt-1 text-3xl font-semibold text-zinc-50">Jarvis voice</h1><p className="mt-1 text-sm text-zinc-400">Local speech, local task brain, one durable conversation.</p></div><Wifi size={20} className="text-zinc-500" /></header><section className="rounded-3xl border border-cyan-500/20 bg-zinc-900 p-6"><div className="flex flex-col items-center gap-4 text-center"><div className={`flex h-40 w-40 items-center justify-center rounded-full border-4 ${voiceState === "listening" ? "border-cyan-300 bg-cyan-400/20" : "border-zinc-700"}`}><button type="button" onClick={voiceState === "speaking" ? () => void interruptSpeech() : voiceState === "stopped" ? () => void startVAD() : stopVAD} className="flex h-28 w-28 items-center justify-center rounded-full bg-cyan-400 text-zinc-950" aria-label="Toggle microphone">{voiceState === "stopped" ? <Mic size={38} /> : <MicOff size={38} />}</button></div><p className="text-lg text-zinc-100">{statusText[voiceState]}</p><div className="flex gap-2"><button type="button" onClick={() => void startVAD()} className="rounded-full border border-zinc-700 px-3 py-1.5 text-xs text-zinc-300">Arm</button><button type="button" onClick={stopVAD} className="rounded-full border border-zinc-700 px-3 py-1.5 text-xs text-zinc-300">Stop</button><button type="button" onClick={() => machine.setMuted(!voiceSnapshot.muted)} className="inline-flex items-center gap-1 rounded-full border border-zinc-700 px-3 py-1.5 text-xs text-zinc-300">{voiceSnapshot.muted ? <VolumeX size={13} /> : <Volume2 size={13} />}{voiceSnapshot.muted ? "Unmute" : "Mute"}</button></div></div></section>{(transcript || lastReply || error) && <section className="space-y-3 rounded-2xl border border-zinc-800 bg-zinc-900/70 p-4">{transcript && <div><p className="text-[11px] uppercase text-zinc-500">You</p><p className="text-zinc-200">{transcript}</p></div>}{lastReply && <div><p className="text-[11px] uppercase text-cyan-400">Jarvis</p><p className="text-zinc-100">{lastReply}</p></div>}{error && <p className="rounded-xl border border-red-500/30 p-3 text-sm text-red-200">{error}</p>}</section>}<form onSubmit={(event) => { event.preventDefault(); const text = textDraft.trim(); setTextDraft(""); void submitCommand(text); }} className="flex gap-2 rounded-2xl border border-zinc-800 bg-zinc-900 p-2"><input value={textDraft} onChange={(event) => setTextDraft(event.target.value)} placeholder="Text fallback — ask Jarvis anything" className="min-w-0 flex-1 bg-transparent px-3 text-sm text-zinc-100 outline-none" /><button type="submit" disabled={!textDraft.trim()} className="inline-flex items-center gap-1 rounded-xl bg-cyan-400 px-3 py-2 text-sm text-zinc-950 disabled:opacity-40"><Send size={15} />Send</button><button type="button" onClick={() => machine.setCloudNextTurn(!voiceSnapshot.cloudNextTurn)} className="rounded-xl border border-zinc-700 px-3 py-2 text-xs text-zinc-400">{voiceSnapshot.cloudNextTurn ? "Cloud next turn" : "Local brain"}</button></form>{questions.length > 0 && <form onSubmit={submitAnswer} className="rounded-2xl border border-amber-500/30 bg-amber-950/20 p-4"><p className="text-xs uppercase text-amber-300">Jarvis needs an answer</p><p className="mt-2 text-sm text-zinc-100">{questions[0].question}</p><div className="mt-3 flex gap-2"><input value={answerDraft} onChange={(event) => setAnswerDraft(event.target.value)} className="min-w-0 flex-1 rounded-xl bg-zinc-950 px-3 py-2 text-sm text-zinc-100" placeholder="Answer Jarvis" /><button type="submit" className="rounded-xl bg-amber-300 px-3 py-2 text-sm text-zinc-950">Answer</button></div></form>}<section className="rounded-2xl border border-zinc-800 bg-zinc-900/60 p-4"><div className="mb-3 flex items-center justify-between"><p className="text-xs uppercase text-zinc-500">Background work</p><span className="text-xs text-zinc-600">{tasks.length ? `${tasks.length} task${tasks.length === 1 ? "" : "s"}` : "None"}</span></div>{tasks.length ? <div className="space-y-2">{tasks.slice(0, 6).map((task) => <div key={task.run_id} className="flex items-center justify-between gap-3 rounded-xl border border-zinc-800 bg-zinc-950/60 px-3 py-2"><div className="min-w-0"><p className="truncate text-sm text-zinc-200">{task.title}</p><p className="text-xs text-zinc-500">{task.status}{task.summary ? ` · ${task.summary}` : ""}</p></div>{["queued", "running", "paused"].includes(task.status) && <button type="button" onClick={() => void cancelVoiceTask(task.run_id).then(async (next) => { await scheduler.dismissQuestion(task.run_id); setTasks((current) => current.map((item) => item.run_id === next.run_id ? next : item)); }).catch((reason) => setError(String(reason)))} className="rounded-lg p-1.5 text-zinc-500 hover:text-red-300" aria-label={`Cancel ${task.title}`}><X size={15} /></button>}</div>)}</div> : <p className="text-sm text-zinc-600">Long-running work will appear here while you keep talking.</p>}</section><section className="grid gap-3 sm:grid-cols-3">{healthCards.map(([label, value]) => <div key={label} className="rounded-2xl border border-zinc-800 bg-zinc-900/60 p-4"><p className="text-xs text-zinc-500">{label}</p><p className="mt-1 text-sm text-zinc-200">{value?.ready ? "Ready" : "Unavailable"}</p></div>)}</section><div className="flex flex-wrap gap-2"><button type="button" onClick={() => void enableAlerts()} className="inline-flex items-center gap-1 rounded-xl border border-zinc-700 px-3 py-2 text-xs text-zinc-300"><Bell size={14} />{alertsEnabled ? "Alerts enabled" : "Enable alerts"}</button>{installPrompt && <button type="button" onClick={() => void installPrompt.prompt().then(() => setInstallPrompt(null))} className="inline-flex items-center gap-1 rounded-xl border border-zinc-700 px-3 py-2 text-xs text-zinc-300"><Download size={14} />Install Jarvis</button>}<span className="inline-flex items-center gap-1 px-2 py-2 text-xs text-zinc-600">{voiceState === "thinking" ? <Loader2 size={13} className="animate-spin" /> : null}{conversationId ? "Server conversation connected" : "Connecting conversation…"}</span></div><footer className="flex items-center justify-between border-t border-zinc-900 pt-4 text-xs text-zinc-600"><span>Updates replay from the durable event cursor.</span><button type="button" onClick={() => void resetConversation()} className="inline-flex items-center gap-1 text-zinc-400"><RotateCcw size={13} />Reset conversation</button></footer></div></div>;
}
