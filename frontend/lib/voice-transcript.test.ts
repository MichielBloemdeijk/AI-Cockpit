import { describe, expect, it } from "vitest";
import type { ConversationEventView, ConversationMessageView } from "./api";
import { mergeVoiceTranscript, voiceTranscriptFromEvent, voiceTranscriptFromMessages } from "./voice-transcript";

const message = (id: string, role: "user" | "assistant" | "system", content: string, sourceEventId: string | null, createdAt: string): ConversationMessageView => ({
  id,
  run_id: null,
  source_event_id: sourceEventId,
  role,
  author_label: role === "assistant" ? "Jarvis" : null,
  content,
  content_format: "text",
  is_final: true,
  created_at: createdAt,
});

const event = (id: string, sequence: number, eventType: string, payload: Record<string, unknown>): ConversationEventView => ({
  id,
  run_id: "run-1",
  sequence,
  branch_key: "main",
  parent_event_id: null,
  actor_kind: "assistant",
  event_type: eventType,
  created_at: `2026-08-25T12:00:${String(sequence).padStart(2, "0")}.000Z`,
  schema_version: 1,
  payload_json: payload,
});

describe("voice transcript state", () => {
  it("converts complete durable message history into chronological user and Jarvis entries", () => {
    const transcript = voiceTranscriptFromMessages([
      message("jarvis", "assistant", "I am here.", "event-2", "2026-08-25T12:00:02.000Z"),
      message("system", "system", "Hidden system note", "event-0", "2026-08-25T12:00:00.000Z"),
      message("user", "user", "Hello", "event-1", "2026-08-25T12:00:01.000Z"),
    ]);

    expect(transcript.map(({ role, content }) => [role, content])).toEqual([
      ["user", "Hello"],
      ["assistant", "I am here."],
    ]);
  });

  it.each([
    ["conversation.assistant.message.completed", { content: "Normal reply" }],
    ["agent.question.asked", { question: "Which folder?", voice_delivery: { speak: true } }],
    ["task.progress", { summary: "Still working" }],
    ["task.completed", { summary: "Task complete" }],
    ["task.failed", { summary: "Task failed" }],
    ["task.interruptions_summarized", { summary: "AI-Cockpit restarted" }],
  ])("projects %s as a visible Jarvis transcript entry", (eventType, payload) => {
    const entry = voiceTranscriptFromEvent(event("event", 4, eventType, payload));
    expect(entry).toMatchObject({ role: "assistant", content: Object.values(payload)[0], sourceEventId: "event" });
  });

  it("keeps one copy when history, SSE, Push, or reconnect contain the same source event", () => {
    const history = voiceTranscriptFromMessages([message("message-1", "assistant", "Task complete", "event-1", "2026-08-25T12:00:01.000Z")]);
    const live = voiceTranscriptFromEvent(event("event-1", 1, "task.completed", { summary: "Task complete" }));
    expect(live).not.toBeNull();

    const afterSse = mergeVoiceTranscript(history, live!);
    const afterPush = mergeVoiceTranscript(afterSse, live!);
    const afterReconnect = mergeVoiceTranscript(afterPush, live!);
    expect(afterReconnect).toEqual(history);
  });

  it("projects a background result even when no speech is played", () => {
    const completion = voiceTranscriptFromEvent(event("event-muted", 9, "task.completed", { summary: "The story is ready." }));
    expect(mergeVoiceTranscript([], completion!)).toMatchObject([
      { role: "assistant", content: "The story is ready.", sourceEventId: "event-muted" },
    ]);
  });
});
