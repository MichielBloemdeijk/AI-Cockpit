/**
 * API client for the AI Cockpit backend.
 * All requests go to /api/* (proxied in dev, same-origin in prod).
 */

function resolveApiBase(): string {
  if (process.env.NEXT_PUBLIC_API_URL) {
    return process.env.NEXT_PUBLIC_API_URL;
  }

  return "";
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
  created_at: string;
}

export interface ConversationDetail extends ConversationSummary {
  active_branch_key: string;
  branches: ConversationBranchView[];
  workspace: ConversationWorkspaceView | null;
  messages: ConversationMessageView[];
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

export interface ConversationEventsResponse {
  conversation_id: string;
  active_branch_key: string;
  events: ConversationEventView[];
  artifacts: ConversationArtifactView[];
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

export async function listConversations(includeArchived = false): Promise<ConversationSummary[]> {
  const res = await fetch(`${BASE}/api/conversations?include_archived=${includeArchived ? "true" : "false"}`, { credentials: "include", cache: "no-store" });
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
): Promise<ConversationEventsResponse> {
  const res = await fetch(`${BASE}/api/conversations/${conversationId}/events?branch_key=${encodeURIComponent(branchKey)}`, {
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

