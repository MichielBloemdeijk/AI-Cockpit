import type {
  ConversationDetail,
  ConversationEventsResponse,
  ConversationSessionMetadata,
  ConversationSummary,
} from "./api";

export const DEFAULT_SESSION_METADATA: ConversationSessionMetadata = {
  mode: "single",
  single_model: "anthropic/claude-sonnet-4-5",
  council_models: ["anthropic/claude-sonnet-4-5"],
  synthesizer_model: "anthropic/claude-sonnet-4-5",
  tool_flags: { workspace_search: true, python_execution: true },
};

export function buildConversationSummary(
  conversationId: string,
  overrides: Partial<ConversationSummary> = {},
): ConversationSummary {
  return {
    id: conversationId,
    title: "Conversation",
    mode_hint: "single",
    session_metadata: DEFAULT_SESSION_METADATA,
    workspace_path: null,
    created_at: "2026-05-01T00:00:00Z",
    updated_at: "2026-05-01T00:00:00Z",
    archived_at: null,
    last_message_preview: null,
    latest_run_status: "completed",
    ...overrides,
  };
}

export function buildConversationDetail(
  conversationId: string,
  overrides: Partial<ConversationDetail> = {},
): ConversationDetail {
  return {
    ...buildConversationSummary(conversationId),
    active_branch_key: "main",
    branches: [],
    workspace: null,
    messages: [],
    ...overrides,
  };
}

export function buildConversationEventsResponse(
  conversationId: string,
  overrides: Partial<ConversationEventsResponse> = {},
): ConversationEventsResponse {
  return {
    conversation_id: conversationId,
    active_branch_key: "main",
    events: [],
    artifacts: [],
    timeline_items: [],
    active_run: null,
    ...overrides,
  };
}