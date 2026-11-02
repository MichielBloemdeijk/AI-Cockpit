/** Conversations: chat and council turns, branches, events and chat settings. */
import { API_BASE, MODEL_REPLY_TIMEOUT_MS, apiRequest } from "./http";

export interface Message {
  role: "user" | "assistant" | "system";
  content: string;
}

export interface ConversationSessionMetadata {
  mode: "single" | "council";
  single_model: string;
  council_models: string[];
  synthesizer_model: string;
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

export interface TimelineItemView {
  item_id: string;
  run_id: string | null;
  sequence: number;
  role: "user" | "assistant" | "system";
  kind: "message";
  content: string;
  /** The author label of a Jarvis message. */
  label?: string | null;
  metadata?: Record<string, unknown> | null;
  /** Committed assistant messages: the reply's usage and, for a council answer, its model responses. */
  usage?: UsageStats | null;
  council?: CouncilResponse | null;
  created_at: string;
}

export interface ActiveRunView {
  run_id: string;
  status: string;
  latest_sequence: number;
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

export interface ChatSettingsResponse {
  available_models: string[];
  defaults: ConversationSessionMetadata;
  task_agent_model: string;
}

export interface ChatSettingsUpdateRequest {
  defaults: ConversationSessionMetadata;
  task_agent_model: string;
}

export interface ConversationBranchResendResponse {
  conversation_id: string;
  branch: ConversationBranchView;
  run_id: string;
  message: ConversationMessageView;
  council_data: CouncilResponse | null;
}

export interface ChatStreamOptions {
  messages: Message[];
  conversationId?: string | null;
  branchKey?: string;
  sessionMetadata?: ConversationSessionMetadata;
  model?: string;
  temperature?: number;
  onMetadata?: (data: { conversation_id: string; run_id: string; model?: string }) => void;
  onEvent?: (event: ConversationEventView) => void;
  onChunk: (chunk: string) => void;
  onDone: () => void;
  onError: (err: string) => void;
  signal?: AbortSignal;
}

/** Streaming chat: a long-lived response read until the server's `done`, so it has no deadline. */
export function streamChat(opts: ChatStreamOptions): void {
  fetch(`${API_BASE}/api/chat`, {
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

export function councilChat(
  messages: Message[],
  temperature = 0.7,
  conversationId?: string | null,
  sessionMetadata?: ConversationSessionMetadata,
): Promise<CouncilResponse> {
  return apiRequest("/api/chat", {
    method: "POST",
    timeoutMs: MODEL_REPLY_TIMEOUT_MS,
    body: {
      messages,
      conversation_id: conversationId,
      session_metadata: sessionMetadata,
      council_mode: true,
      stream: false,
      temperature,
    },
  });
}

export function listConversations(
  includeArchived = false,
  kind: "all" | "standard" | "jarvis" = "all",
  archivedOnly = false,
): Promise<ConversationSummary[]> {
  const params = new URLSearchParams({
    include_archived: includeArchived ? "true" : "false",
    kind,
  });
  if (archivedOnly) params.set("archived_only", "true");
  return apiRequest(`/api/conversations?${params}`);
}

export function getConversation(conversationId: string, branchKey = "main"): Promise<ConversationDetail> {
  return apiRequest(`/api/conversations/${encodeURIComponent(conversationId)}?branch_key=${encodeURIComponent(branchKey)}`);
}

export function getConversationEvents(
  conversationId: string,
  branchKey = "main",
  afterSequence?: number,
): Promise<ConversationEventsResponse> {
  const cursor = afterSequence === undefined ? "" : `&after_sequence=${Math.max(0, Math.trunc(afterSequence))}`;
  return apiRequest(`/api/conversations/${encodeURIComponent(conversationId)}/events?branch_key=${encodeURIComponent(branchKey)}${cursor}`);
}

export function createConversation(
  title?: string,
  modeHint?: string,
  sessionMetadata?: ConversationSessionMetadata,
  initialMessage?: string,
): Promise<ConversationCreateResponse> {
  return apiRequest("/api/conversations", {
    method: "POST",
    timeoutMs: MODEL_REPLY_TIMEOUT_MS,
    body: { title, mode_hint: modeHint, session_metadata: sessionMetadata, initial_message: initialMessage },
  });
}

export function createConversationMessage(
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
  return apiRequest(`/api/conversations/${encodeURIComponent(conversationId)}/messages`, {
    method: "POST", timeoutMs: MODEL_REPLY_TIMEOUT_MS, body: request,
  });
}

export function archiveConversation(conversationId: string): Promise<ConversationSummary> {
  return apiRequest(`/api/conversations/${encodeURIComponent(conversationId)}/archive`, { method: "POST" });
}

export function unarchiveConversation(conversationId: string): Promise<ConversationSummary> {
  return apiRequest(`/api/conversations/${encodeURIComponent(conversationId)}/unarchive`, { method: "POST" });
}

export function resendConversationBranch(
  conversationId: string,
  request: { source_message_id: string; content: string; parent_branch_key: string; label?: string },
): Promise<ConversationBranchResendResponse> {
  return apiRequest(`/api/conversations/${encodeURIComponent(conversationId)}/branches/resend`, {
    method: "POST", timeoutMs: MODEL_REPLY_TIMEOUT_MS, body: request,
  });
}

export function getModels(): Promise<{ council_models: string[]; synthesizer_model: string }> {
  return apiRequest("/api/chat/models");
}

export function getChatSettings(): Promise<ChatSettingsResponse> {
  return apiRequest("/api/chat/settings");
}

export function updateChatSettings(request: ChatSettingsUpdateRequest): Promise<ChatSettingsResponse> {
  return apiRequest("/api/chat/settings", { method: "PUT", body: request });
}

/** Native SSE plus a cheap durable catch-up poll; the stream itself has no deadline. */
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
    `${API_BASE}/api/conversations/${encodeURIComponent(conversationId)}/stream?after_sequence=${afterSequence}`,
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
