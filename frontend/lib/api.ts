/**
 * API client for the AI Cockpit backend.
 * All requests go to /api/* (proxied in dev, same-origin in prod).
 */

function resolveApiBase(): string {
  return process.env.NEXT_PUBLIC_API_URL?.trim().replace(/\/$/, "") ?? "";
}

const BASE = resolveApiBase();
const REQUEST_TIMEOUT_MESSAGE = "Request timed out. Please try again.";
const DEFAULT_PAGE_LOAD_TIMEOUT_MS = 10000;
const AUTH_CHECK_TIMEOUT_MS = 5000;

async function fetchWithTimeout(
  input: string,
  init: RequestInit,
  timeoutMs: number,
): Promise<Response> {
  const controller = new AbortController();
  const upstreamSignal = init.signal;

  if (upstreamSignal) {
    if (upstreamSignal.aborted) {
      controller.abort(upstreamSignal.reason);
    } else {
      upstreamSignal.addEventListener("abort", () => controller.abort(upstreamSignal.reason), { once: true });
    }
  }

  const timeoutId = setTimeout(() => controller.abort(new Error(REQUEST_TIMEOUT_MESSAGE)), timeoutMs);

  try {
    return await fetch(input, { ...init, signal: controller.signal });
  } catch (error) {
    if (controller.signal.aborted && !upstreamSignal?.aborted) {
      throw new Error(REQUEST_TIMEOUT_MESSAGE);
    }
    throw error;
  } finally {
    clearTimeout(timeoutId);
  }
}

// ─── Types ────────────────────────────────────────────────────────────────────

export interface Message {
  role: "user" | "assistant" | "system";
  content: string;
}

export interface ChatToolFlags {
  workspace_search: boolean;
  python_execution: boolean;
}

export interface ConversationSessionMetadata {
  mode: "single" | "council";
  single_model: string;
  council_models: string[];
  synthesizer_model: string;
  tool_flags: ChatToolFlags;
}

export interface UsageStats {
  prompt_tokens?: number;
  completion_tokens?: number;
  total_tokens?: number;
  cost?: number;
  prompt_tokens_details?: Record<string, number>;
  completion_tokens_details?: Record<string, number>;
  cost_details?: Record<string, number>;
  is_byok?: boolean;
  server_tool_use?: Record<string, number>;
}

export interface ModelResponse {
  model: string;
  content: string;
  usage?: UsageStats;
  error?: string;
}

export interface CouncilResponse {
  conversation_id?: string;
  run_id?: string;
  model_responses: ModelResponse[];
  synthesized: string;
  synthesizer_model: string;
  synthesizer_usage?: UsageStats;
  total_usage?: UsageStats;
}

export interface ChatResponse {
  conversation_id: string;
  run_id: string;
  model: string;
  content: string;
  usage?: UsageStats;
  error?: string;
}

export interface ConversationSummary {
  id: string;
  title: string | null;
  mode_hint: string | null;
  session_metadata: ConversationSessionMetadata | null;
  workspace_path: string | null;
  created_at: string;
  updated_at: string;
  archived_at: string | null;
  last_message_preview: string | null;
  latest_run_status: string | null;
  conversation_kind?: "standard" | "jarvis_daily";
  lifecycle_status?: "open" | "closed";
  closed_at?: string | null;
  jarvis_day_key?: string | null;
  jarvis_day_sequence?: number | null;
  is_current_jarvis?: boolean;
  is_live?: boolean;
  is_read_only?: boolean;
}

export interface ConversationBranchView {
  branch_key: string;
  label: string | null;
  parent_branch_key: string | null;
  branched_from_message_id: string | null;
  created_at: string;
}

export interface WorkspaceFileView {
  path: string;
  size: number;
  updated_at: string | null;
}

export interface ConversationWorkspaceView {
  path: string;
  files: WorkspaceFileView[];
}

export interface ConversationMessageView {
  id: string;
  run_id: string | null;
  source_event_id: string | null;
  role: "user" | "assistant" | "system";
  author_label: string | null;
  content: string;
  content_format: string;
  is_final: boolean;
  modality?: "voice" | "text" | "system";
  execution_mode?: "jarvis_shell" | "agent" | "council";
  communication_state?: "completed" | "interrupted" | "unheard" | "transcription_failed";
  source_task_id?: string | null;
  source_task_event_id?: string | null;
  presented_in_turn_id?: string | null;
  created_at: string;
}

export interface ConversationDetail extends ConversationSummary {
  active_branch_key: string;
  branches: ConversationBranchView[];
  workspace: ConversationWorkspaceView | null;
  messages: ConversationMessageView[];
  active_run?: ActiveRunView | null;
}

export interface ConversationEventView {
  id: string;
  run_id: string | null;
  sequence: number;
  branch_key: string | null;
  parent_event_id: string | null;
  actor_kind: string;
  event_type: string;
  created_at: string;
  schema_version: number;
  payload_json?: Record<string, unknown> | null;
}

export interface AgentStreamUpdate {
  kind:
    | "thought_delta"
    | "thought_done"
    | "text_delta"
    | "text_done"
    | "summary_delta"
    | "summary_done"
    | "tool_started"
    | "tool_progress"
    | "tool_done"
    | "progress";
  run_id: string;
  step: number;
  activity_id?: string;
  tool_call_id?: string;
  tool?: string;
  ok?: boolean;
  delta?: string;
  content?: string;
}

export interface ConversationArtifactView {
  id: string;
  run_id: string | null;
  source_event_id: string | null;
  artifact_type: string;
  mime_type: string;
  content_text: string | null;
  content_json?: Record<string, unknown> | null;
  created_at: string;
}

export interface TimelineSectionView {
  title: string;
  items: string[];
}

export interface TimelineItemView {
  item_id: string;
  run_id: string | null;
  sequence: number;
  role: "user" | "assistant" | "system";
  kind: "message" | "status" | "plan" | "thought" | "tool_call" | "tool_result" | "question" | "answer" | "summary" | "error" | "task";
  content: string;
  title?: string | null;
  label?: string | null;
  tone?: "default" | "info" | "success" | "warning" | "error" | null;
  status?: string | null;
  step?: number | null;
  tool_name?: string | null;
  badges?: string[];
  code?: string | null;
  sections?: TimelineSectionView[];
  metadata?: Record<string, unknown> | null;
  created_at: string;
}

export interface ActiveRunView {
  run_id: string;
  status: string;
  latest_sequence: number;
  live_item: TimelineItemView | null;
}

export interface ConversationEventsResponse {
  conversation_id: string;
  active_branch_key: string;
  events: ConversationEventView[];
  artifacts: ConversationArtifactView[];
  timeline_items: TimelineItemView[];
  active_run: ActiveRunView | null;
}

export interface ConversationCreateResponse {
  conversation: ConversationSummary;
  run_id: string | null;
}

export interface ConversationTurnResponse {
  conversation_id: string;
  run_id: string;
  message: ConversationMessageView;
  council_data: CouncilResponse | null;
}

export interface ConversationToolResult {
  tool: string;
  output: string;
  metadata?: Record<string, unknown> | null;
}

export interface ConversationToolResponse {
  conversation_id: string;
  run_id: string;
  message: ConversationMessageView;
  result: ConversationToolResult;
}

export interface ChatSettingsResponse {
  available_models: string[];
  defaults: ConversationSessionMetadata;
  task_agent_model: string;
}

export interface ChatSettingsUpdateRequest {
  defaults: ConversationSessionMetadata;
  task_agent_model: string;
}

export interface MemoryItemView {
  id: string;
  scope: string;
  kind: string;
  title: string;
  content: string;
  status: string;
  confidence: number | null;
  source_conversation_id: string;
  source_event_id: string | null;
  knowledge_path: string | null;
  created_at: string;
  reviewed_at: string | null;
  deleted_at: string | null;
}

export interface ConversationBranchResendResponse {
  conversation_id: string;
  branch: ConversationBranchView;
  run_id: string;
  message: ConversationMessageView;
  council_data: CouncilResponse | null;
}

export interface KnowledgeReviewItemView extends MemoryItemView {
  conversation_title: string | null;
}

export interface KnowledgeDocumentView {
  path: string;
  title: string;
  kind: string;
  content: string;
  updated_at: string | null;
}

export interface GeneratedAppSummary {
  id: string;
  slug: string;
  title: string;
  description: string | null;
  status: string;
  route_path: string;
  verification_status: string | null;
  source_task_run_id: string | null;
  source_conversation_id: string | null;
  updated_at: string;
  created_at: string;
}

export interface GeneratedAppDetail extends GeneratedAppSummary {
  frontend_root: string;
  frontend_entry_path: string | null;
  icon_asset_path: string | null;
  cover_asset_path: string | null;
  manifest_json: Record<string, unknown> | null;
  last_error: string | null;
  allowed_write_roots: string[];
}

export interface GeneratedAppCreate {
  title: string;
  slug?: string;
  description?: string;
  status?: string;
  verification_status?: string;
  source_task_run_id?: string;
  source_conversation_id?: string;
  manifest_json?: Record<string, unknown>;
}

export interface GeneratedAppUpdate {
  title?: string;
  description?: string;
  status?: string;
  verification_status?: string;
  frontend_entry_path?: string;
  icon_asset_path?: string;
  cover_asset_path?: string;
  manifest_json?: Record<string, unknown>;
  last_error?: string;
  source_task_run_id?: string;
  source_conversation_id?: string;
}

// ─── Auth ─────────────────────────────────────────────────────────────────────

export async function login(password: string): Promise<void> {
  let res: Response;
  try {
    res = await fetch(`${BASE}/api/auth/login`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      credentials: "include",
      body: JSON.stringify({ password }),
    });
  } catch {
    throw new Error("Unable to reach the backend. Check that the app is running.");
  }

  if (res.status === 401) {
    throw new Error("Invalid password.");
  }

  if (!res.ok) {
    throw new Error(`Login failed (HTTP ${res.status}).`);
  }
}

export async function logout(): Promise<void> {
  await fetch(`${BASE}/api/auth/logout`, { method: "POST", credentials: "include" });
}

export async function checkAuth(): Promise<boolean> {
  try {
    const res = await fetchWithTimeout(`${BASE}/api/auth/status`, { credentials: "include" }, AUTH_CHECK_TIMEOUT_MS);
    return res.ok;
  } catch {
    return false;
  }
}

// ─── Chat ─────────────────────────────────────────────────────────────────────

export interface ChatStreamOptions {
  messages: Message[];
  conversationId?: string | null;
  branchKey?: string;
  sessionMetadata?: ConversationSessionMetadata;
  model?: string;
  temperature?: number;
  onMetadata?: (data: { conversation_id: string; run_id: string; model?: string }) => void;
  onEvent?: (event: ConversationEventView) => void;
  onAgentStream?: (update: AgentStreamUpdate) => void;
  onChunk: (chunk: string) => void;
  onDone: () => void;
  onError: (err: string) => void;
  signal?: AbortSignal;
}

export function streamChat(opts: ChatStreamOptions): void {
  fetch(`${BASE}/api/chat`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    credentials: "include",
    signal: opts.signal,
    body: JSON.stringify({
      messages: opts.messages,
      conversation_id: opts.conversationId,
      branch_key: opts.branchKey ?? "main",
      session_metadata: opts.sessionMetadata,
      model: opts.model,
      stream: true,
      council_mode: false,
      temperature: opts.temperature ?? 0.7,
    }),
  }).then(async (res) => {
    if (!res.ok) {
      opts.onError(`HTTP ${res.status}`);
      return;
    }
    const reader = res.body!.getReader();
    const decoder = new TextDecoder();
    let buffer = "";
    let completed = false;

    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, { stream: true });
      const lines = buffer.split("\n");
      buffer = lines.pop() ?? "";
      for (const line of lines) {
        if (!line.startsWith("data: ")) continue;
        try {
          const parsed = JSON.parse(line.slice(6));
          if (parsed.type === "metadata") opts.onMetadata?.(parsed);
          else if (parsed.type === "event" && parsed.event) opts.onEvent?.(parsed.event as ConversationEventView);
          else if (parsed.type === "agent_stream" && parsed.stream) opts.onAgentStream?.(parsed.stream as AgentStreamUpdate);
          else if (parsed.type === "chunk") opts.onChunk(parsed.content);
          else if (parsed.type === "done") {
            completed = true;
            opts.onDone();
          } else if (parsed.type === "error") opts.onError(parsed.message);
        } catch {}
      }
    }
    if (!completed) opts.onDone();
  }).catch((err) => {
    if (err.name === "AbortError") {
      opts.onDone();
      return;
    }
    opts.onError(String(err));
  });
}

export async function councilChat(
  messages: Message[],
  temperature = 0.7,
  conversationId?: string | null,
  sessionMetadata?: ConversationSessionMetadata,
): Promise<CouncilResponse> {
  const res = await fetch(`${BASE}/api/chat`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    credentials: "include",
    body: JSON.stringify({
      messages,
      conversation_id: conversationId,
      session_metadata: sessionMetadata,
      council_mode: true,
      stream: false,
      temperature,
    }),
  });
  if (!res.ok) throw new Error(`HTTP ${res.status}`);
  return res.json();
}

export async function listConversations(
  includeArchived = false,
  kind: "all" | "standard" | "jarvis" = "all",
  archivedOnly = false,
): Promise<ConversationSummary[]> {
  const params = new URLSearchParams({
    include_archived: includeArchived ? "true" : "false",
    kind,
  });
  if (archivedOnly) params.set("archived_only", "true");
  const res = await fetch(`${BASE}/api/conversations?${params}`, { credentials: "include", cache: "no-store" });
  if (!res.ok) throw new Error(`HTTP ${res.status}`);
  return res.json();
}

export interface TaskSummary {
  id: string;
  origin_surface: string;
  origin_conversation_id: string | null;
  origin_turn_id: string | null;
  goal: string;
  status: "queued" | "waiting_for_worker" | "running" | "waiting_for_input" | "waiting_until" | "completed" | "failed" | "interrupted" | "cancelled";
  status_reason: string | null;
  creation_order: number;
  current_attempt_id: string | null;
  cancellation_requested_at: string | null;
  importance: string;
  presentation_policy: string;
  terminal_summary: string | null;
  waiting_until: string | null;
  resume_at: string | null;
  wait_reason: string | null;
  scheduled_delivery: Record<string, unknown> | null;
  scheduled_delivered_at: string | null;
  queue_position: number | null;
  created_at: string;
  updated_at: string;
  finished_at: string | null;
}

export interface TaskAttempt {
  id: string;
  attempt_number: number;
  worker_id: string | null;
  workspace_path: string | null;
  checkpoint: Record<string, unknown> | null;
  claimed_at: string | null;
  heartbeat_at: string | null;
  started_at: string | null;
  ended_at: string | null;
  terminal_reason: string | null;
  agent_runtime_reference: string | null;
  usage_summary: Record<string, unknown> | null;
  created_at: string;
}

export interface TaskEvent {
  id: string;
  sequence: number;
  task_id: string;
  attempt_id: string | null;
  event_type: string;
  payload: Record<string, unknown> | null;
  causality: Record<string, unknown> | null;
  created_at: string;
}

export interface TaskQuestion {
  id: string;
  asked_attempt_id: string | null;
  answered_attempt_id: string | null;
  source_task_event_id: string | null;
  question: string;
  answer: string | null;
  state: string;
  asked_at: string;
  answered_at: string | null;
}

export interface TaskArtifact {
  id: string;
  attempt_id: string | null;
  source_task_event_id: string | null;
  artifact_type: string;
  mime_type: string;
  title: string | null;
  uri: string | null;
  content_text: string | null;
  content_json: Record<string, unknown> | null;
  created_at: string;
}

export interface TaskTarget {
  id: string;
  resource_type: string;
  resource_key: string;
  mutable: boolean;
  discovered_at: string;
}

export interface TaskPresentation {
  id: string;
  candidate_type: string;
  coalescing_key: string;
  importance: string;
  state: string;
  source_task_event_id: string | null;
  created_at: string;
  presented_at: string | null;
}

export interface TaskDetail extends TaskSummary {
  attempts: TaskAttempt[];
  events: TaskEvent[];
  questions: TaskQuestion[];
  artifacts: TaskArtifact[];
  targets: TaskTarget[];
  presentations: TaskPresentation[];
  worker_health: { status: string; worker_id: string | null; heartbeat_at: string | null };
}

export interface TaskDispatch {
  goal: string;
  idempotency_key: string;
  origin_surface?: string;
  origin_conversation_id?: string;
  origin_turn_id?: string;
  importance?: string;
  presentation_policy?: string;
  targets?: Array<{ resource_type: string; resource_key: string; mutable?: boolean }>;
}

export async function getJarvisState(): Promise<JarvisStateResponse> {
  const res = await fetch(`${BASE}/api/jarvis/state`, { credentials: "include", cache: "no-store" });
  if (!res.ok) throw new Error(`HTTP ${res.status}`);
  return res.json();
}

export function jarvisLiveUrl(cursor = 0): string {
  const params = new URLSearchParams({ cursor: String(cursor) });
  return `${BASE}/api/jarvis/live?${params}`;
}

export async function getJarvisLiveEvents(
  after = 0,
  limit = 250,
): Promise<JarvisLiveEventReplayResponse> {
  const params = new URLSearchParams({
    after: String(Math.max(0, after)),
    limit: String(Math.max(1, Math.min(1000, limit))),
  });
  const res = await fetch(`${BASE}/api/jarvis/live/replay?${params}`, {
    credentials: "include",
    cache: "no-store",
  });
  if (!res.ok) throw new Error(`HTTP ${res.status}`);
  return res.json();
}

export async function getJarvisPreferences(): Promise<JarvisPreferences> {
  const res = await fetch(`${BASE}/api/jarvis/preferences`, { credentials: "include", cache: "no-store" });
  if (!res.ok) throw new Error(`HTTP ${res.status}`);
  return res.json();
}

export async function updateJarvisPreferences(
  patch: Partial<Pick<JarvisPreferences, "voice" | "concision" | "wake_behavior" | "developer_mode" | "android_privacy">>,
): Promise<JarvisPreferences> {
  const res = await fetch(`${BASE}/api/jarvis/preferences`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    credentials: "include",
    body: JSON.stringify(patch),
  });
  if (!res.ok) throw new Error(`HTTP ${res.status}`);
  return res.json();
}

export interface JarvisPairingCode {
  code: string;
  expires_at: string;
}

export async function createJarvisPairingCode(): Promise<JarvisPairingCode> {
  const res = await fetch(`${BASE}/api/jarvis/devices/pairing-code`, {
    method: "POST",
    credentials: "include",
  });
  if (!res.ok) throw new Error(`HTTP ${res.status}`);
  return res.json();
}

export async function acceptJarvisTextTurn(
  content: string,
  idempotencyKey: string,
): Promise<JarvisTurnAcceptedResponse> {
  const res = await fetch(`${BASE}/api/jarvis/turns/text`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    credentials: "include",
    body: JSON.stringify({ content, idempotency_key: idempotencyKey }),
  });
  if (!res.ok) throw new Error(`HTTP ${res.status}`);
  return res.json();
}

export async function startFreshJarvisLog(idempotencyKey: string): Promise<ConversationSummary> {
  const res = await fetch(`${BASE}/api/jarvis/logs/new`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    credentials: "include",
    body: JSON.stringify({ idempotency_key: idempotencyKey }),
  });
  if (!res.ok) throw new Error(`HTTP ${res.status}`);
  const payload = await res.json() as { current_log: ConversationSummary };
  return payload.current_log;
}

export async function getJarvisLogEvents(
  conversationId: string,
  after = 0,
): Promise<JarvisConversationEventReplayResponse> {
  const params = new URLSearchParams({ after: String(after) });
  const res = await fetch(`${BASE}/api/jarvis/logs/${encodeURIComponent(conversationId)}/events?${params}`, {
    credentials: "include",
    cache: "no-store",
  });
  if (!res.ok) throw new Error(`HTTP ${res.status}`);
  return res.json();
}

export async function getJarvisSessionEvents(
  sessionId: string,
  after = 0,
): Promise<JarvisSessionEventReplayResponse> {
  const params = new URLSearchParams({ after: String(after) });
  const res = await fetch(`${BASE}/api/jarvis/sessions/${encodeURIComponent(sessionId)}/events?${params}`, {
    credentials: "include",
    cache: "no-store",
  });
  if (!res.ok) throw new Error(`HTTP ${res.status}`);
  return res.json();
}

export async function getTaskEvents(after = 0): Promise<TaskEventReplayResponse> {
  const params = new URLSearchParams({ after: String(after) });
  const res = await fetch(`${BASE}/api/tasks/events?${params}`, {
    credentials: "include",
    cache: "no-store",
  });
  if (!res.ok) throw new Error(`HTTP ${res.status}`);
  return res.json();
}

export async function createJarvisRealtimeCall(
  controllerId: string,
  role: "controller" | "observer" = "controller",
  providerAdapter: "fake" | "openai" | "openai_live" = "fake",
  sdpOffer?: string,
  clientContext: JarvisClientContext = currentJarvisClientContext(),
  activationContext?: JarvisRealtimeActivationContext,
): Promise<JarvisRealtimeCallResponse> {
  const res = await fetch(`${BASE}/api/jarvis/realtime/calls`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    credentials: "include",
    body: JSON.stringify({
      controller_id: controllerId,
      role,
      provider_adapter: providerAdapter,
      client_context: clientContext,
      ...(activationContext ? { activation_context: activationContext } : {}),
      ...(sdpOffer ? { sdp_offer: sdpOffer } : {}),
    }),
  });
  if (!res.ok) {
    let detail = `HTTP ${res.status}`;
    try { detail = (await res.json() as { detail?: string }).detail || detail; } catch {}
    throw new Error(detail);
  }
  return res.json();
}

export interface JarvisClientContext {
  time_zone: string;
  utc_offset_minutes: number;
  observed_at: string;
}

export interface JarvisRealtimeActivationContext {
  source: "wake_word";
  wake_score: number;
  activation_id: string;
  wake_detected_at?: string;
  local_speech_started_at?: string;
  gate_mode?: "transcript_rules" | "immediate";
  benchmark_run_id?: string | null;
  benchmark_fixture?: string | null;
}

export interface JarvisRealtimeActivationRequest {
  generation: number;
  wake_score: number;
  activation_id: string;
  wake_detected_at?: string;
  local_speech_started_at?: string;
  gate_mode?: "transcript_rules" | "immediate";
  benchmark_run_id?: string | null;
  benchmark_fixture?: string | null;
}

export interface JarvisRealtimeActivationResponse {
  session_id: string;
  generation: number;
  activation_id: string;
  gate_state: "pending" | "accepted" | "rejected" | "timed_out";
  gate_mode: "transcript_rules" | "immediate";
  gate_timeout_ms: number;
  audio_policy: JarvisRealtimeAudioPolicy;
}

export interface JarvisRealtimeAudioPolicy {
  configuration_id: string;
  turn_mode: "server_vad" | "semantic_vad" | "client_vad";
  commit_owner: "provider" | "client";
  silence_duration_ms: number | null;
  vad_threshold: number | null;
  prefix_padding_ms: number | null;
  semantic_eagerness: "low" | "medium" | "high" | "auto" | null;
  create_response: false;
  interrupt_response: false;
}

export interface JarvisBargeInRequest {
  generation: number;
  speech_event_id: string;
  source: "browser_local_vad" | "browser_provider_vad";
  request_id: string;
  response_id: string | null;
  input_item_id: string | null;
}

export interface JarvisBargeInResponse {
  accepted: boolean;
}

export async function registerJarvisRealtimeActivation(
  sessionId: string,
  request: JarvisRealtimeActivationRequest,
): Promise<JarvisRealtimeActivationResponse> {
  const res = await fetch(`${BASE}/api/jarvis/realtime/sessions/${encodeURIComponent(sessionId)}/activations`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    credentials: "include",
    body: JSON.stringify(request),
  });
  if (!res.ok) {
    let detail = `HTTP ${res.status}`;
    try { detail = (await res.json() as { detail?: string }).detail || detail; } catch {}
    throw new Error(detail);
  }
  return res.json();
}

export async function bargeInJarvisRealtime(
  sessionId: string,
  request: JarvisBargeInRequest,
): Promise<JarvisBargeInResponse> {
  const res = await fetch(
    `${BASE}/api/jarvis/realtime/sessions/${encodeURIComponent(sessionId)}/barge-in`,
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      credentials: "include",
      body: JSON.stringify(request),
    },
  );
  if (!res.ok) {
    let detail = `HTTP ${res.status}`;
    try { detail = (await res.json() as { detail?: string }).detail || detail; } catch {}
    throw new Error(detail);
  }
  return res.json();
}

export interface JarvisRealtimeTelemetryEvent {
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

export async function submitJarvisRealtimeTelemetry(
  events: JarvisRealtimeTelemetryEvent[],
): Promise<number> {
  if (!events.length) return 0;
  const res = await fetch(`${BASE}/api/jarvis/realtime/telemetry`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    credentials: "include",
    keepalive: true,
    body: JSON.stringify({ events: events.slice(0, 100) }),
  });
  if (!res.ok) throw new Error(`HTTP ${res.status}`);
  const payload = await res.json() as { accepted?: number };
  return typeof payload.accepted === "number" ? payload.accepted : 0;
}

export function currentJarvisClientContext(now = new Date()): JarvisClientContext {
  return {
    time_zone: Intl.DateTimeFormat().resolvedOptions().timeZone || "UTC",
    utc_offset_minutes: -now.getTimezoneOffset(),
    observed_at: now.toISOString(),
  };
}

export async function injectFakeJarvisEvent(
  sessionId: string,
  request: JarvisFakeProviderEventRequest,
): Promise<void> {
  const res = await fetch(`${BASE}/api/jarvis/fake/sessions/${encodeURIComponent(sessionId)}/events`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    credentials: "include",
    body: JSON.stringify(request),
  });
  if (!res.ok) throw new Error(`HTTP ${res.status}`);
}

export async function closeJarvisRealtimeSession(sessionId: string, generation: number): Promise<void> {
  const params = new URLSearchParams({ generation: String(generation) });
  const res = await fetch(`${BASE}/api/jarvis/realtime/sessions/${encodeURIComponent(sessionId)}?${params}`, {
    method: "DELETE",
    credentials: "include",
    keepalive: true,
  });
  if (!res.ok) throw new Error(`HTTP ${res.status}`);
}

export async function listTasks(statuses: string[] = []): Promise<TaskSummary[]> {
  const params = new URLSearchParams();
  statuses.forEach((status) => params.append("status", status));
  const suffix = params.size ? `?${params}` : "";
  const res = await fetch(`${BASE}/api/tasks${suffix}`, { credentials: "include", cache: "no-store" });
  if (!res.ok) throw new Error(`HTTP ${res.status}`);
  return res.json();
}

export async function getTask(taskId: string): Promise<TaskDetail> {
  const res = await fetch(`${BASE}/api/tasks/${encodeURIComponent(taskId)}`, {
    credentials: "include",
    cache: "no-store",
  });
  if (!res.ok) throw new Error(`HTTP ${res.status}`);
  return res.json();
}

export async function getTaskWorkerHealth(): Promise<{ status: string; worker_id: string | null; heartbeat_at: string | null }> {
  const res = await fetch(`${BASE}/api/tasks/worker`, { credentials: "include", cache: "no-store" });
  if (!res.ok) throw new Error(`HTTP ${res.status}`);
  return res.json();
}

export async function dispatchTask(request: TaskDispatch): Promise<TaskSummary> {
  const res = await fetch(`${BASE}/api/tasks`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    credentials: "include",
    body: JSON.stringify(request),
  });
  if (!res.ok) throw new Error(`HTTP ${res.status}`);
  return res.json();
}

async function taskAction(taskId: string, action: "cancel" | "retry"): Promise<TaskSummary> {
  const res = await fetch(`${BASE}/api/tasks/${encodeURIComponent(taskId)}/${action}`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    credentials: "include",
    body: JSON.stringify({ idempotency_key: crypto.randomUUID() }),
  });
  if (!res.ok) throw new Error(`HTTP ${res.status}`);
  return res.json();
}

export async function cancelTask(taskId: string): Promise<TaskSummary> {
  return taskAction(taskId, "cancel");
}

export async function retryTask(taskId: string): Promise<TaskSummary> {
  return taskAction(taskId, "retry");
}

export async function answerTaskQuestion(
  taskId: string,
  questionId: string,
  answer: string,
): Promise<TaskSummary> {
  const res = await fetch(
    `${BASE}/api/tasks/${encodeURIComponent(taskId)}/questions/${encodeURIComponent(questionId)}/answer`,
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      credentials: "include",
      body: JSON.stringify({ answer, idempotency_key: crypto.randomUUID() }),
    },
  );
  if (!res.ok) throw new Error(`HTTP ${res.status}`);
  return res.json();
}

export async function getConversation(conversationId: string, branchKey = "main"): Promise<ConversationDetail> {
  const res = await fetch(`${BASE}/api/conversations/${conversationId}?branch_key=${encodeURIComponent(branchKey)}`, {
    credentials: "include",
    cache: "no-store",
  });
  if (!res.ok) throw new Error(`HTTP ${res.status}`);
  return res.json();
}

export async function getConversationEvents(
  conversationId: string,
  branchKey = "main",
  afterSequence?: number,
): Promise<ConversationEventsResponse> {
  const cursor = afterSequence === undefined ? "" : `&after_sequence=${Math.max(0, Math.trunc(afterSequence))}`;
  const res = await fetch(`${BASE}/api/conversations/${encodeURIComponent(conversationId)}/events?branch_key=${encodeURIComponent(branchKey)}${cursor}`, {
    credentials: "include",
    cache: "no-store",
  });
  if (!res.ok) throw new Error(`HTTP ${res.status}`);
  return res.json();
}

export async function createConversation(
  title?: string,
  modeHint?: string,
  sessionMetadata?: ConversationSessionMetadata,
  initialMessage?: string,
): Promise<ConversationCreateResponse> {
  const res = await fetch(`${BASE}/api/conversations`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    credentials: "include",
    body: JSON.stringify({ title, mode_hint: modeHint, session_metadata: sessionMetadata, initial_message: initialMessage }),
  });
  if (!res.ok) throw new Error(`HTTP ${res.status}`);
  return res.json();
}

export async function createConversationMessage(
  conversationId: string,
  request: {
    content: string;
    branch_key?: string;
    session_metadata?: ConversationSessionMetadata;
    model?: string;
    temperature?: number;
    max_tokens?: number;
  },
): Promise<ConversationTurnResponse> {
  const res = await fetch(`${BASE}/api/conversations/${conversationId}/messages`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    credentials: "include",
    body: JSON.stringify(request),
  });
  if (!res.ok) throw new Error(`HTTP ${res.status}`);
  return res.json();
}

export async function archiveConversation(conversationId: string): Promise<ConversationSummary> {
  const res = await fetch(`${BASE}/api/conversations/${conversationId}/archive`, {
    method: "POST",
    credentials: "include",
  });
  if (!res.ok) throw new Error(`HTTP ${res.status}`);
  return res.json();
}

export async function unarchiveConversation(conversationId: string): Promise<ConversationSummary> {
  const res = await fetch(`${BASE}/api/conversations/${conversationId}/unarchive`, {
    method: "POST",
    credentials: "include",
  });
  if (!res.ok) throw new Error(`HTTP ${res.status}`);
  return res.json();
}

export async function resendConversationBranch(
  conversationId: string,
  request: { source_message_id: string; content: string; parent_branch_key: string; label?: string },
): Promise<ConversationBranchResendResponse> {
  const res = await fetch(`${BASE}/api/conversations/${conversationId}/branches/resend`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    credentials: "include",
    body: JSON.stringify(request),
  });
  if (!res.ok) throw new Error(`HTTP ${res.status}`);
  return res.json();
}

export async function getModels(): Promise<{ council_models: string[]; synthesizer_model: string }> {
  const res = await fetch(`${BASE}/api/chat/models`, { credentials: "include" });
  if (!res.ok) throw new Error(`HTTP ${res.status}`);
  return res.json();
}

export async function getChatSettings(): Promise<ChatSettingsResponse> {
  const res = await fetch(`${BASE}/api/chat/settings`, { credentials: "include" });
  if (!res.ok) throw new Error(`HTTP ${res.status}`);
  return res.json();
}

export async function updateChatSettings(
  request: ChatSettingsUpdateRequest,
): Promise<ChatSettingsResponse> {
  const res = await fetch(`${BASE}/api/chat/settings`, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    credentials: "include",
    body: JSON.stringify(request),
  });
  if (!res.ok) throw new Error(`HTTP ${res.status}`);
  return res.json();
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
  state: string;
  required_facts: Record<string, unknown> | null;
  created_at: string;
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

export interface TaskEventReplayResponse {
  latest_sequence: number;
  events: TaskEvent[];
}

export interface JarvisRealtimeCallResponse {
  role: "controller" | "observer";
  request_id?: string | null;
  session: JarvisSession | null;
  sdp_answer: string | null;
  rotate_after_seconds: number | null;
  audio_policy: JarvisRealtimeAudioPolicy | null;
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

export async function getVapidPublicKey(): Promise<{ enabled: boolean; public_key: string }> {
  const res = await fetch(`${BASE}/api/notifications/vapid-public-key`, { credentials: "include", cache: "no-store" });
  if (!res.ok) throw new Error(`HTTP ${res.status}`);
  return res.json();
}

export async function savePushSubscription(subscription: PushSubscription): Promise<{ id: string; endpoint: string }> {
  const json = subscription.toJSON();
  const res = await fetch(`${BASE}/api/notifications/subscriptions`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    credentials: "include",
    body: JSON.stringify({ endpoint: json.endpoint, keys: json.keys, user_agent: navigator.userAgent }),
  });
  if (!res.ok) throw new Error(`HTTP ${res.status}`);
  return res.json();
}

export async function acknowledgePushDelivery(deliveryId: string, state: "opened" | "spoken"): Promise<void> {
  await fetch(`${BASE}/api/notifications/deliveries/${encodeURIComponent(deliveryId)}/ack`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    credentials: "include",
    body: JSON.stringify({ state }),
  });
}

export function streamConversationEvents(
  conversationId: string,
  afterSequence: number,
  onEvent: (event: ConversationEventView) => void,
  onError?: (error: Error) => void,
): AbortController {
  const controller = new AbortController();
  let cursor = Math.max(0, afterSequence);
  let polling = false;
  const deliver = (event: ConversationEventView) => {
    if (event.sequence <= cursor) return;
    cursor = event.sequence;
    onEvent(event);
  };
  const source = new EventSource(
    `${BASE}/api/conversations/${encodeURIComponent(conversationId)}/stream?after_sequence=${afterSequence}`,
    { withCredentials: true },
  );
  const receive = (rawEvent: Event) => {
    const message = rawEvent as MessageEvent<string>;
    try {
      const payload = JSON.parse(message.data) as { event?: ConversationEventView };
      if (payload.event) deliver(payload.event);
    } catch {
      // Ignore malformed or non-event frames. The durable cursor makes a
      // reconnect safe without inventing a partial event locally.
    }
  };
  source.addEventListener("conversation.event", receive);
  source.onerror = () => {
    source.close();
    if (!controller.signal.aborted) onError?.(new Error("Conversation event stream disconnected"));
  };
  // Some mobile/WebView proxies leave an SSE request open but do not release
  // small frames promptly. A cheap durable catch-up poll keeps the voice UI
  // correct in that case; sequence filtering makes it a no-op when SSE works.
  const pollTimer = setInterval(() => {
    if (polling || controller.signal.aborted) return;
    polling = true;
    void getConversationEvents(conversationId, "main", cursor)
      .then((response) => response.events
        .filter((event) => event.sequence > cursor)
        .sort((left, right) => left.sequence - right.sequence)
        .forEach(deliver))
      .catch((reason) => {
        if (!controller.signal.aborted) onError?.(reason instanceof Error ? reason : new Error(String(reason)));
      })
      .finally(() => { polling = false; });
  }, 750);
  controller.signal.addEventListener("abort", () => {
    source.close();
    clearInterval(pollTimer);
  }, { once: true });
  return controller;
}

export async function extractConversationMemoryItems(conversationId: string): Promise<MemoryItemView[]> {
  const res = await fetch(`${BASE}/api/conversations/${conversationId}/memory-items/extract`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    credentials: "include",
    body: JSON.stringify({}),
  });
  if (!res.ok) throw new Error(`HTTP ${res.status}`);
  return res.json();
}

export async function listKnowledgeReviewItems(status = "proposed"): Promise<{ items: KnowledgeReviewItemView[] }> {
  const res = await fetch(`${BASE}/api/knowledge/review-items?status=${encodeURIComponent(status)}`, {
    credentials: "include",
  });
  if (!res.ok) throw new Error(`HTTP ${res.status}`);
  return res.json();
}

export async function listKnowledgeDocuments(): Promise<{ documents: KnowledgeDocumentView[] }> {
  const res = await fetch(`${BASE}/api/knowledge/documents`, { credentials: "include" });
  if (!res.ok) throw new Error(`HTTP ${res.status}`);
  return res.json();
}

export async function approveKnowledgeItem(memoryItemId: string): Promise<{ memory_item: MemoryItemView; knowledge_path: string }> {
  const res = await fetch(`${BASE}/api/knowledge/memory-items/${memoryItemId}/approve`, {
    method: "POST",
    credentials: "include",
  });
  if (!res.ok) throw new Error(`HTTP ${res.status}`);
  return res.json();
}

export async function rejectKnowledgeItem(memoryItemId: string): Promise<MemoryItemView> {
  const res = await fetch(`${BASE}/api/knowledge/memory-items/${memoryItemId}/reject`, {
    method: "POST",
    credentials: "include",
  });
  if (!res.ok) throw new Error(`HTTP ${res.status}`);
  return res.json();
}

export async function deleteKnowledgeItem(memoryItemId: string): Promise<MemoryItemView> {
  const res = await fetch(`${BASE}/api/knowledge/memory-items/${memoryItemId}`, {
    method: "DELETE",
    credentials: "include",
  });
  if (!res.ok) throw new Error(`HTTP ${res.status}`);
  return res.json();
}

export async function executeConversationTool(
  conversationId: string,
  request: {
    tool: "workspace_search" | "python_execution";
    branch_key?: string;
    query?: string;
    code?: string;
    working_directory?: string;
  },
): Promise<ConversationToolResponse> {
  const res = await fetch(`${BASE}/api/conversations/${conversationId}/tools`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    credentials: "include",
    body: JSON.stringify(request),
  });
  if (!res.ok) throw new Error(`HTTP ${res.status}`);
  return res.json();
}

// ─── Apps ─────────────────────────────────────────────────────────────────────

export async function listGeneratedApps(): Promise<GeneratedAppSummary[]> {
  const res = await fetchWithTimeout(`${BASE}/api/apps`, { credentials: "include" }, DEFAULT_PAGE_LOAD_TIMEOUT_MS);
  if (!res.ok) throw new Error(`HTTP ${res.status}`);
  return res.json();
}

export async function createGeneratedApp(req: GeneratedAppCreate): Promise<GeneratedAppDetail> {
  const res = await fetch(`${BASE}/api/apps`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    credentials: "include",
    body: JSON.stringify(req),
  });
  if (!res.ok) throw new Error(`HTTP ${res.status}`);
  return res.json();
}

export async function getGeneratedApp(appId: string): Promise<GeneratedAppDetail> {
  const res = await fetch(`${BASE}/api/apps/${appId}`, { credentials: "include" });
  if (!res.ok) throw new Error(`HTTP ${res.status}`);
  return res.json();
}

export async function getGeneratedAppBySlug(slug: string): Promise<GeneratedAppDetail> {
  const res = await fetch(`${BASE}/api/apps/slug/${encodeURIComponent(slug)}`, { credentials: "include" });
  if (!res.ok) throw new Error(`HTTP ${res.status}`);
  return res.json();
}

export async function updateGeneratedApp(appId: string, req: GeneratedAppUpdate): Promise<GeneratedAppDetail> {
  const res = await fetch(`${BASE}/api/apps/${appId}`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    credentials: "include",
    body: JSON.stringify(req),
  });
  if (!res.ok) throw new Error(`HTTP ${res.status}`);
  return res.json();
}

