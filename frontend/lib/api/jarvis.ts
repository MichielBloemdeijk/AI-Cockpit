/** Jarvis: the daily log, live voice calls and replay, preferences and linked phones. */
import { API_BASE, apiRequest } from "./http";
import type { ConversationDetail, ConversationEventView, ConversationMessageView, ConversationSummary } from "./conversations";
import type { AttentionState, TaskSummary } from "./tasks";

export interface JarvisPairingCode {
  code: string;
  expires_at: string;
  server_url: string;
  link: string;
  qr_svg: string;
}

export interface LinkedDevice { id: string; label: string; last_seen_at: string | null }

export interface JarvisClientContext {
  time_zone: string;
  utc_offset_minutes: number;
  observed_at: string;
}

export interface JarvisLiveActivationContext {
  source: "wake_word";
  wake_score: number;
  activation_id: string;
  wake_detected_at?: string;
  local_speech_started_at?: string;
  gate_mode?: "transcript_rules" | "immediate";
  benchmark_run_id?: string | null;
  benchmark_fixture?: string | null;
}

export interface JarvisLiveTelemetryEvent {
  trace_id: string;
  client_sequence?: number;
  activation_id?: string;
  session_id?: string;
  generation?: number;
  request_id?: string;
  provider_response_id?: string;
  provider_input_item_id?: string;
  source: "browser" | "android" | "backend" | "provider";
  event_type: string;
  observed_at: string;
  monotonic_ms?: number;
  duration_ms?: number;
  detail?: Record<string, unknown>;
}

export interface JarvisStateResponse {
  operational_day: string;
  state_revision: number;
  situation_revision: number;
  live_event_cursor: number;
  current_log: ConversationDetail | null;
  tasks: TaskSummary[];
  pending_questions: JarvisPendingQuestion[];
  pending_presentations: JarvisPresentation[];
  session: JarvisSession | null;
  worker_health: { status: string; worker_id: string | null; heartbeat_at: string | null };
  component_health: Record<string, {
    status: string;
    detail?: string | null;
    heartbeat_at?: string | null;
  }>;
  cursors: JarvisDomainCursors;
  focused_task_id: string | null;
  recent_task_refs: string[];
}

export interface JarvisPreferences {
  voice: "marin" | "cedar";
  concision: "concise" | "balanced";
  wake_behavior: "manual" | "wake_word" | "follow_up";
  developer_mode: boolean;
  android_privacy: Record<string, unknown>;
  time_zone: string;
  updated_at: string | null;
}

export interface JarvisLiveEvent {
  id: string;
  sequence: number;
  event_type: string;
  source_domain: "conversation" | "task" | "session" | "notification";
  source_id: string | null;
  source_sequence: number | null;
  payload: Record<string, unknown> | null;
  created_at: string;
}

export interface JarvisLiveEventReplayResponse {
  latest_sequence: number;
  events: JarvisLiveEvent[];
}

export interface JarvisDomainCursors {
  conversation: number;
  task: number;
  session: number;
}

export interface JarvisSession {
  id: string;
  conversation_id: string;
  generation: number;
  provider_adapter: string;
  lifecycle_state: string;
  controller_id: string | null;
  floor_state: string;
  last_event_sequence: number;
  created_at: string;
  ready_at: string | null;
  disconnected_at: string | null;
  client_time_zone: string | null;
  client_utc_offset_minutes: number | null;
  client_observed_at: string | null;
}

export interface JarvisPendingQuestion {
  task_id: string;
  question_id: string;
  question: string;
  asked_at: string;
}

export interface JarvisPresentation {
  id: string;
  task_id: string;
  candidate_type: string;
  importance: string;
  state: AttentionState;
  required_facts: Record<string, unknown> | null;
  created_at: string;
  handled_at: string | null;
  handled_by: string | null;
}

export interface JarvisSessionEvent {
  id: string;
  session_id: string;
  sequence: number;
  event_type: string;
  payload: Record<string, unknown> | null;
  created_at: string;
}

export interface JarvisConversationEventReplayResponse {
  conversation_id: string;
  latest_sequence: number;
  events: ConversationEventView[];
}

export interface JarvisSessionEventReplayResponse {
  session_id: string;
  latest_sequence: number;
  events: JarvisSessionEvent[];
}

export interface JarvisLiveCallResponse {
  role: "controller" | "observer";
  request_id?: string | null;
  session: JarvisSession | null;
  sdp_answer: string | null;
  provider_capabilities: Record<string, boolean>;
  server_received_at: string;
  server_responded_at: string;
}

export interface JarvisFakeProviderEventRequest {
  generation: number;
  event_id: string;
  kind:
    | "ready"
    | "user_speaking"
    | "user_stopped"
    | "user_transcript_delta"
    | "user_transcript"
    | "assistant_generating"
    | "assistant_speaking"
    | "assistant_transcript_delta"
    | "assistant_response"
    | "assistant_interrupted"
    | "shell_call"
    | "disconnected"
    | "provider_error";
  payload?: Record<string, unknown>;
}

export interface JarvisTurnAcceptedResponse {
  conversation: ConversationSummary;
  message: ConversationMessageView;
  event_sequence: number;
}

/** Provider calls exchange an SDP offer and answer, so they get more time than ordinary reads. */
const LIVE_CALL_TIMEOUT_MS = 30_000;

export function getJarvisState(signal?: AbortSignal): Promise<JarvisStateResponse> {
  return apiRequest("/api/jarvis/state", { signal });
}

/** The durable event stream (SSE); long-lived, so it does not use the finite transport. */
export function jarvisLiveUrl(cursor = 0): string {
  return `${API_BASE}/api/jarvis/live?${new URLSearchParams({ cursor: String(cursor) })}`;
}

export function getJarvisLiveEvents(after = 0, limit = 250, signal?: AbortSignal): Promise<JarvisLiveEventReplayResponse> {
  const params = new URLSearchParams({
    after: String(Math.max(0, after)),
    limit: String(Math.max(1, Math.min(1000, limit))),
  });
  return apiRequest(`/api/jarvis/live/replay?${params}`, { signal });
}

export function getJarvisPreferences(): Promise<JarvisPreferences> {
  return apiRequest("/api/jarvis/preferences");
}

export function updateJarvisPreferences(
  patch: Partial<Pick<JarvisPreferences, "voice" | "concision" | "wake_behavior" | "developer_mode" | "android_privacy">>,
): Promise<JarvisPreferences> {
  return apiRequest("/api/jarvis/preferences", { method: "PATCH", body: patch });
}

export function getLinkedDevices(): Promise<LinkedDevice[]> {
  return apiRequest("/api/jarvis/devices");
}

export async function unlinkDevice(id: string): Promise<void> {
  await apiRequest(`/api/jarvis/devices/${encodeURIComponent(id)}`, { method: "DELETE" });
}

export function createJarvisPairingCode(): Promise<JarvisPairingCode> {
  return apiRequest("/api/jarvis/devices/pairing-code", { method: "POST" });
}

export function acceptJarvisTextTurn(content: string, idempotencyKey: string): Promise<JarvisTurnAcceptedResponse> {
  return apiRequest("/api/jarvis/turns/text", { method: "POST", body: { content, idempotency_key: idempotencyKey } });
}

export async function startFreshJarvisLog(idempotencyKey: string): Promise<ConversationSummary> {
  const payload = await apiRequest<{ current_log: ConversationSummary }>("/api/jarvis/logs/new", {
    method: "POST", body: { idempotency_key: idempotencyKey },
  });
  return payload.current_log;
}

export function getJarvisLogEvents(conversationId: string, after = 0): Promise<JarvisConversationEventReplayResponse> {
  return apiRequest(`/api/jarvis/logs/${encodeURIComponent(conversationId)}/events?${new URLSearchParams({ after: String(after) })}`);
}

export function getJarvisSessionEvents(sessionId: string, after = 0): Promise<JarvisSessionEventReplayResponse> {
  return apiRequest(`/api/jarvis/live/sessions/${encodeURIComponent(sessionId)}/events?${new URLSearchParams({ after: String(after) })}`);
}

export function createJarvisLiveCall(
  controllerId: string,
  role: "controller" | "observer" = "controller",
  providerAdapter: "fake" | "openai_live" = "fake",
  sdpOffer?: string,
  clientContext: JarvisClientContext = currentJarvisClientContext(),
  activationContext?: JarvisLiveActivationContext,
): Promise<JarvisLiveCallResponse> {
  return apiRequest("/api/jarvis/live/calls", {
    method: "POST",
    timeoutMs: LIVE_CALL_TIMEOUT_MS,
    body: {
      controller_id: controllerId,
      role,
      provider_adapter: providerAdapter,
      client_context: clientContext,
      ...(activationContext ? { activation_context: activationContext } : {}),
      ...(sdpOffer ? { sdp_offer: sdpOffer } : {}),
    },
  });
}

/** Best effort: telemetry never fails the caller. Returns how many events were accepted. */
export async function submitJarvisLiveTelemetry(events: JarvisLiveTelemetryEvent[]): Promise<number> {
  if (!events.length) return 0;
  try {
    const payload = await apiRequest<{ accepted?: number }>("/api/jarvis/live/telemetry", {
      method: "POST", keepalive: true, body: { events: events.slice(0, 100) },
    });
    return typeof payload?.accepted === "number" ? payload.accepted : 0;
  } catch {
    return 0;
  }
}

export function currentJarvisClientContext(now = new Date()): JarvisClientContext {
  return {
    time_zone: Intl.DateTimeFormat().resolvedOptions().timeZone || "UTC",
    utc_offset_minutes: -now.getTimezoneOffset(),
    observed_at: now.toISOString(),
  };
}

export async function injectFakeJarvisEvent(sessionId: string, request: JarvisFakeProviderEventRequest): Promise<void> {
  await apiRequest(`/api/jarvis/fake/sessions/${encodeURIComponent(sessionId)}/events`, { method: "POST", body: request });
}

export async function closeJarvisLiveSession(sessionId: string, generation: number): Promise<void> {
  await apiRequest(`/api/jarvis/live/sessions/${encodeURIComponent(sessionId)}?${new URLSearchParams({ generation: String(generation) })}`, {
    method: "DELETE", keepalive: true,
  });
}

export async function submitJarvisPlayback(sessionId: string, payload: Record<string, unknown>): Promise<void> {
  await apiRequest(`/api/jarvis/live/sessions/${encodeURIComponent(sessionId)}/playback`, { method: "POST", body: payload });
}
