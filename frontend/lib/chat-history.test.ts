import { describe, expect, it } from "vitest";

import type { ConversationArtifactView, ConversationDetail, ConversationEventView } from "./api";
import { mapConversationMessages } from "./chat-history";


describe("mapConversationMessages", () => {
  it("attaches stored council artifacts to the synthesized assistant message", () => {
    const detail: ConversationDetail = {
      id: "conversation-1",
      title: "Council thread",
      mode_hint: "council",
      created_at: "2026-04-21T00:00:00Z",
      updated_at: "2026-04-21T00:00:00Z",
      archived_at: null,
      last_message_preview: "Synthesized answer",
      latest_run_status: "completed",
      messages: [
        {
          id: "message-1",
          run_id: "run-1",
          source_event_id: "event-1",
          role: "user",
          author_label: "You",
          content: "Compare options",
          content_format: "markdown",
          is_final: true,
          created_at: "2026-04-21T00:00:00Z",
        },
        {
          id: "message-2",
          run_id: "run-1",
          source_event_id: "event-2",
          role: "assistant",
          author_label: "synthesizer",
          content: "Synthesized answer",
          content_format: "markdown",
          is_final: true,
          created_at: "2026-04-21T00:00:01Z",
        },
      ],
    };

    const artifacts: ConversationArtifactView[] = [
      {
        id: "artifact-1",
        run_id: "run-1",
        source_event_id: "event-3",
        artifact_type: "council.model.response",
        mime_type: "application/json",
        content_text: null,
        content_json: { model: "model-a", content: "Option A", usage: { cost: 0.1, total_tokens: 10 } },
        created_at: "2026-04-21T00:00:01Z",
      },
      {
        id: "artifact-2",
        run_id: "run-1",
        source_event_id: "event-4",
        artifact_type: "council.synthesis.response",
        mime_type: "application/json",
        content_text: null,
        content_json: { model: "synthesizer", content: "Synthesized answer", usage: { cost: 0.2, total_tokens: 20 } },
        created_at: "2026-04-21T00:00:02Z",
      },
    ];

    const events: ConversationEventView[] = [];

    const messages = mapConversationMessages(detail, artifacts, events);

    expect(messages).toHaveLength(2);
    expect(messages[1].councilData).toMatchObject({
      run_id: "run-1",
      synthesized: "Synthesized answer",
      synthesizer_model: "synthesizer",
      total_usage: { cost: 0.30000000000000004, total_tokens: 30 },
    });
    expect(messages[1].councilData?.model_responses).toEqual([
      { model: "model-a", content: "Option A", usage: { cost: 0.1, total_tokens: 10 }, error: undefined },
    ]);
    expect(messages[1].usage).toEqual({ cost: 0.30000000000000004, total_tokens: 30 });
  });

  it("keeps tool arguments and output behind compact timeline summaries", () => {
    const detail: ConversationDetail = {
      id: "conversation-2",
      title: "Agent thread",
      mode_hint: "single",
      created_at: "2026-05-01T00:00:00Z",
      updated_at: "2026-05-01T00:00:00Z",
      archived_at: null,
      last_message_preview: "Done",
      latest_run_status: "completed",
      messages: [],
    };

    const events: ConversationEventView[] = [
      {
        id: "event-1",
        run_id: "run-2",
        sequence: 1,
        branch_key: "main",
        parent_event_id: null,
        actor_kind: "assistant",
        event_type: "agent.tool.called",
        created_at: "2026-05-01T00:00:01Z",
        schema_version: 1,
        payload_json: {
          step: 1,
          tool: "file_read",
          arguments: { path: "frontend/lib/chat-history.ts" },
        },
      },
      {
        id: "event-2",
        run_id: "run-2",
        sequence: 2,
        branch_key: "main",
        parent_event_id: null,
        actor_kind: "assistant",
        event_type: "agent.tool.completed",
        created_at: "2026-05-01T00:00:02Z",
        schema_version: 1,
        payload_json: {
          step: 1,
          tool: "file_read",
          arguments: { path: "frontend/lib/chat-history.ts" },
          ok: true,
          output: "export function mapConversationMessages() {}",
        },
      },
    ];

    const messages = mapConversationMessages(detail, [], events);

    // tool_call is replaced in-place by tool_result → only one entry
    expect(messages).toHaveLength(1);
    expect(messages[0].kind).toBe("tool_result");
    expect(messages[0].title).toBe("file read");
    expect(messages[0].content).toBe("lib/chat-history.ts");
    expect(messages[0].code).toContain("mapConversationMessages");
  });

  it("keeps thought summaries separate from the final tool result row", () => {
    const detail: ConversationDetail = {
      id: "conversation-3",
      title: "Agent thread",
      mode_hint: "single",
      created_at: "2026-05-01T00:00:00Z",
      updated_at: "2026-05-01T00:00:00Z",
      archived_at: null,
      last_message_preview: "Done",
      latest_run_status: "completed",
      messages: [],
    };

    const events: ConversationEventView[] = [
      {
        id: "event-1",
        run_id: "run-3",
        sequence: 1,
        branch_key: "main",
        parent_event_id: null,
        actor_kind: "assistant",
        event_type: "agent.thought.summary",
        created_at: "2026-05-01T00:00:01Z",
        schema_version: 1,
        payload_json: {
          step: 2,
          thought: "Use tool file_read to inspect frontend/lib/chat-history.ts",
        },
      },
      {
        id: "event-2",
        run_id: "run-3",
        sequence: 2,
        branch_key: "main",
        parent_event_id: null,
        actor_kind: "assistant",
        event_type: "agent.tool.completed",
        created_at: "2026-05-01T00:00:02Z",
        schema_version: 1,
        payload_json: {
          step: 2,
          tool: "file_read",
          arguments: { path: "frontend/lib/chat-history.ts" },
          ok: true,
          output: "export function mapConversationMessages() {}",
        },
      },
    ];

    const messages = mapConversationMessages(detail, [], events);

    expect(messages).toHaveLength(2);
    expect(messages[0].kind).toBe("thought");
    expect(messages[0].content).toContain("Use tool file_read");
    expect(messages[1].kind).toBe("tool_result");
    expect(messages[1].title).toBe("file read");
    expect(messages[1].content).toBe("lib/chat-history.ts");
  });

  it("maps persisted progress summaries as visible status rows", () => {
    const detail: ConversationDetail = {
      id: "conversation-5",
      title: "Agent thread",
      mode_hint: "single",
      created_at: "2026-05-01T00:00:00Z",
      updated_at: "2026-05-01T00:00:00Z",
      archived_at: null,
      last_message_preview: "Done",
      latest_run_status: "completed",
      messages: [],
    };

    const events: ConversationEventView[] = [
      {
        id: "event-progress-1",
        run_id: "run-5",
        sequence: 1,
        branch_key: "main",
        parent_event_id: null,
        actor_kind: "assistant",
        event_type: "agent.progress.summary",
        created_at: "2026-05-01T00:00:01Z",
        schema_version: 1,
        payload_json: {
          step: 1,
          summary: "Inspecting the chat history mapping.",
        },
      },
    ];

    const messages = mapConversationMessages(detail, [], events);

    expect(messages).toHaveLength(1);
    expect(messages[0].kind).toBe("status");
    expect(messages[0].title).toBe("Step 1");
    expect(messages[0].content).toBe("Inspecting the chat history mapping.");
  });

  it("suppresses low-signal persisted tool-intent thought and progress rows", () => {
    const detail: ConversationDetail = {
      id: "conversation-6",
      title: "Agent thread",
      mode_hint: "single",
      created_at: "2026-05-01T00:00:00Z",
      updated_at: "2026-05-01T00:00:00Z",
      archived_at: null,
      last_message_preview: "Done",
      latest_run_status: "completed",
      messages: [],
    };

    const events: ConversationEventView[] = [
      {
        id: "event-thought-1",
        run_id: "run-6",
        sequence: 1,
        branch_key: "main",
        parent_event_id: null,
        actor_kind: "assistant",
        event_type: "agent.thought.summary",
        created_at: "2026-05-01T00:00:01Z",
        schema_version: 1,
        payload_json: { step: 1, thought: "Use tool file_read" },
      },
      {
        id: "event-progress-1",
        run_id: "run-6",
        sequence: 2,
        branch_key: "main",
        parent_event_id: null,
        actor_kind: "assistant",
        event_type: "agent.progress.summary",
        created_at: "2026-05-01T00:00:02Z",
        schema_version: 1,
        payload_json: { step: 1, summary: "Use tool file_read" },
      },
      {
        id: "event-tool-1",
        run_id: "run-6",
        sequence: 3,
        branch_key: "main",
        parent_event_id: null,
        actor_kind: "assistant",
        event_type: "agent.tool.completed",
        created_at: "2026-05-01T00:00:03Z",
        schema_version: 1,
        payload_json: {
          step: 1,
          tool: "file_read",
          arguments: { path: "frontend/lib/chat-history.ts" },
          ok: true,
          output: "export function mapConversationMessages() {}",
        },
      },
    ];

    const messages = mapConversationMessages(detail, [], events);

    expect(messages).toHaveLength(1);
    expect(messages[0].kind).toBe("tool_result");
  });

  it("maps conversation.task.started with expandable task metadata details", () => {
    const detail: ConversationDetail = {
      id: "conversation-4",
      title: "Task bridge thread",
      mode_hint: "single",
      created_at: "2026-05-01T00:00:00Z",
      updated_at: "2026-05-01T00:00:00Z",
      archived_at: null,
      last_message_preview: "Started app task",
      latest_run_status: "running",
      messages: [],
    };

    const events: ConversationEventView[] = [
      {
        id: "event-task-started",
        run_id: "run-4",
        sequence: 1,
        branch_key: "main",
        parent_event_id: null,
        actor_kind: "assistant",
        event_type: "conversation.task.started",
        created_at: "2026-05-01T00:00:01Z",
        schema_version: 1,
        payload_json: {
          task: {
            id: "cb994bdb-9459-4ccf-a757-7488b8728532",
            title: "Make the background brighter",
          },
          app: {
            route_path: "/apps/create-a-basic-app-that-says-hello-world",
            frontend_root: "frontend/app/apps/create-a-basic-app-that-says-hello-world",
            frontend_entry_path: "frontend/app/apps/create-a-basic-app-that-says-hello-world/page.tsx",
            allowed_write_roots: [
              "frontend/app/apps/create-a-basic-app-that-says-hello-world",
              "frontend/public/apps/create-a-basic-app-that-says-hello-world",
            ],
          },
        },
      },
    ];

    const messages = mapConversationMessages(detail, [], events);

    expect(messages).toHaveLength(1);
    expect(messages[0].kind).toBe("status");
    expect(messages[0].title).toBe("Started app task");
    expect(messages[0].content).toContain("Make the background brighter");
    expect(messages[0].code).toContain("Task id: cb994bdb-9459-4ccf-a757-7488b8728532");
    expect(messages[0].code).toContain("App root: frontend/app/apps/create-a-basic-app-that-says-hello-world");
  });

  it("maps conversation.task.start_failed as a warning status row", () => {
    const detail: ConversationDetail = {
      id: "conversation-5",
      title: "Task bridge thread",
      mode_hint: "single",
      created_at: "2026-05-01T00:00:00Z",
      updated_at: "2026-05-01T00:00:00Z",
      archived_at: null,
      last_message_preview: "Task start status: failed",
      latest_run_status: "failed",
      messages: [],
    };

    const events: ConversationEventView[] = [
      {
        id: "event-task-failed",
        run_id: "run-5",
        sequence: 1,
        branch_key: "main",
        parent_event_id: null,
        actor_kind: "assistant",
        event_type: "conversation.task.start_failed",
        created_at: "2026-05-01T00:00:01Z",
        schema_version: 1,
        payload_json: {
          task_start: {
            status: "failed",
            error: "App task goal is required",
          },
        },
      },
    ];

    const messages = mapConversationMessages(detail, [], events);

    expect(messages).toHaveLength(1);
    expect(messages[0].kind).toBe("status");
    expect(messages[0].tone).toBe("warning");
    expect(messages[0].title).toBe("Task start failed");
    expect(messages[0].code).toContain("Task start status: failed");
    expect(messages[0].code).toContain("App task goal is required");
  });

  it("drops the redundant agent.run.started row while keeping later progress events", () => {
    const detail: ConversationDetail = {
      id: "conversation-6",
      title: "Agent thread",
      mode_hint: "single",
      created_at: "2026-05-01T00:00:00Z",
      updated_at: "2026-05-01T00:00:00Z",
      archived_at: null,
      last_message_preview: "Done",
      latest_run_status: "running",
      messages: [],
    };

    const events: ConversationEventView[] = [
      {
        id: "event-run-started",
        run_id: "run-6",
        sequence: 1,
        branch_key: "main",
        parent_event_id: null,
        actor_kind: "assistant",
        event_type: "agent.run.started",
        created_at: "2026-05-01T00:00:01Z",
        schema_version: 1,
        payload_json: { goal: "Create the page" },
      },
      {
        id: "event-plan-created",
        run_id: "run-6",
        sequence: 2,
        branch_key: "main",
        parent_event_id: null,
        actor_kind: "assistant",
        event_type: "agent.plan.created",
        created_at: "2026-05-01T00:00:02Z",
        schema_version: 1,
        payload_json: {
          summary: "Create the page and verify it.",
          steps: ["Create the page", "Verify it"],
          open_questions: [],
          assumptions: [],
        },
      },
    ];

    const messages = mapConversationMessages(detail, [], events);

    expect(messages).toHaveLength(1);
    expect(messages[0].title).toBe("Plan ready");
  });

  it("marks persisted user messages as non-branchable after unsafe mutations", () => {
    const detail: ConversationDetail = {
      id: "conversation-7",
      title: "Unsafe branch thread",
      mode_hint: "single",
      created_at: "2026-05-01T00:00:00Z",
      updated_at: "2026-05-01T00:00:00Z",
      archived_at: null,
      last_message_preview: "Changed files",
      latest_run_status: "completed",
      messages: [
        {
          id: "message-1",
          run_id: "run-7",
          source_event_id: "event-1",
          role: "user",
          author_label: "You",
          content: "Please update the file",
          content_format: "markdown",
          is_final: true,
          created_at: "2026-05-01T00:00:00Z",
        },
      ],
    };

    const events: ConversationEventView[] = [
      {
        id: "event-complete-1",
        run_id: "run-7",
        sequence: 1,
        branch_key: "main",
        parent_event_id: null,
        actor_kind: "assistant",
        event_type: "agent.run.completed",
        created_at: "2026-05-01T00:00:01Z",
        schema_version: 1,
        payload_json: {
          run_summary: {
            changed_files: ["frontend/app/page.tsx"],
          },
        },
      },
    ];

    const messages = mapConversationMessages(detail, [], events);

    expect(messages[0].branchable).toBe(false);
    expect(messages[0].branchBlockReason).toContain("mutating actions");
  });
});