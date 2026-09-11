import { describe, expect, it } from "vitest";
import { reduceJarvisRealtimeTurns, summarizeJarvisRealtimeTurns } from "./jarvis-realtime-turns";

const event = (kind: string, monotonic_ms: number, extra: Record<string, unknown> = {}) => ({
  kind,
  monotonic_ms,
  wall_time: "2026-09-08T12:00:00.000Z",
  session_id: "session-1",
  generation: 2,
  activation_id: "activation-1",
  configuration_id: "server_vad:s350:t0.5:p300",
  ...extra,
});

describe("Jarvis realtime v3 turn reducer", () => {
  it("correlates reordered duplicate events and does not require a transcript for success", () => {
    const entries = [
      event("first_playable_media", 1_000, { response_id: "response-1", id: "playback-1" }),
      event("response.created", 700, { request_id: "request-1", response_id: "response-1", input_item_id: "item-1", id: "response-created" }),
      event("response.output_audio.delta", 900, { response_id: "response-1", id: "audio-1" }),
      event("response.done", 1_100, { response_id: "response-1", detail: { status: "completed" }, id: "done-1" }),
      event("response.done", 1_100, { response_id: "response-1", detail: { status: "completed" }, id: "done-1" }),
      event("response.create.sent", 600, { request_id: "request-1", input_item_id: "item-1", id: "create-1" }),
      event("wake.gate.accepted", 500, { id: "gate-1" }),
      event("provider.user_input_committed", 400, { item_id: "item-1", id: "commit-1" }),
      event("local_speech_stopped", 300, { id: "speech-end-1" }),
    ];

    const [result] = reduceJarvisRealtimeTurns(entries);
    expect(result).toMatchObject({
      schema: "jarvis.realtime_turn_result.v3",
      turn_key: "activation-1",
      input_item_id: "item-1",
      request_id: "request-1",
      response_id: "response-1",
      generation_outcome: "completed",
      playback_state: "observed",
      transcript_state: "missing",
      gate_state: "accepted",
      configuration_id: "server_vad:s350:t0.5:p300",
    });
    expect(result.latency_ms).toEqual({
      local_speech_end_to_provider_commit: 100,
      provider_commit_to_gate_ready: 0,
      gate_ready_to_response_create_sent: 200,
      response_create_sent_to_response_created: 100,
      response_created_to_first_provider_audio: 200,
      first_provider_audio_to_playback_proxy: 100,
    });
  });

  it("keeps a follow-up item separate from the wake activation", () => {
    const entries = [
      event("provider.user_input_committed", 100, { item_id: "wake-item", id: "wake-commit" }),
      event("wake.gate.accepted", 110, { id: "wake-gate" }),
      event("response.created", 200, { request_id: "wake-request", response_id: "wake-response", input_item_id: "wake-item", id: "wake-response-created" }),
      event("assistant_response", 300, { request_id: "wake-request", response_id: "wake-response", id: "wake-terminal" }),
      event("provider.user_input_committed", 400, { activation_id: undefined, item_id: "follow-up-item", id: "follow-up-commit" }),
      event("response.created", 500, { activation_id: undefined, request_id: "follow-up-request", response_id: "follow-up-response", input_item_id: "follow-up-item", id: "follow-up-response-created" }),
      event("assistant_response", 600, { activation_id: undefined, request_id: "follow-up-request", response_id: "follow-up-response", id: "follow-up-terminal" }),
    ];

    const results = summarizeJarvisRealtimeTurns(entries).turns;
    expect(results).toHaveLength(2);
    expect(results.map((result) => result.turn_key)).toEqual([
      "activation-1",
      "session-1:2:follow-up-item",
    ]);
  });

  it("merges activation-only and item-only states when a bridge event arrives", () => {
    const results = reduceJarvisRealtimeTurns([
      event("wake.gate.accepted", 100, { id: "gate" }),
      event("input_audio_buffer.committed", 200, {
        activation_id: undefined,
        item_id: "item-1",
        id: "browser-commit",
      }),
      event("provider.user_input_committed", 210, { item_id: "item-1", id: "backend-commit" }),
      event("response.done", 300, {
        item_id: "item-1",
        response_id: "response-1",
        detail: { status: "completed" },
        id: "done",
      }),
    ]);

    expect(results).toHaveLength(1);
    expect(results[0]).toMatchObject({
      turn_key: "activation-1",
      input_item_id: "item-1",
      gate_state: "accepted",
      generation_outcome: "completed",
    });
  });

  it.each([
    ["cancelled", "cancelled"],
    ["failed", "failed"],
  ] as const)("uses explicit response.done status %s", (status, expected) => {
    const [result] = reduceJarvisRealtimeTurns([
      event("provider.user_input_committed", 100, { item_id: "item-1", id: "commit" }),
      event("response.done", 200, {
        item_id: "item-1",
        response_id: "response-1",
        detail: { status },
        id: "done",
      }),
    ]);
    expect(result.generation_outcome).toBe(expected);
  });

  it("uses only the local speech boundary for local-to-provider latency", () => {
    const [result] = reduceJarvisRealtimeTurns([
      event("local_speech_stopped", 100, { id: "local-stop" }),
      event("input_audio_buffer.speech_stopped", 180, { item_id: "item-1", id: "provider-stop" }),
      event("provider.user_input_committed", 200, { item_id: "item-1", id: "commit" }),
      event("response.done", 300, { item_id: "item-1", detail: { status: "completed" }, id: "done" }),
    ]);
    expect(result.latency_ms.local_speech_end_to_provider_commit).toBe(100);
  });

  it("preserves benchmark labels in the terminal turn result", () => {
    const [result] = reduceJarvisRealtimeTurns([
      event("provider.user_input_committed", 100, {
        item_id: "item-1",
        benchmark_run_id: "run-42",
        benchmark_fixture: "quiet-room",
        id: "commit",
      }),
      event("response.done", 200, { item_id: "item-1", detail: { status: "completed" }, id: "done" }),
    ]);
    expect(result).toMatchObject({
      benchmark_run_id: "run-42",
      benchmark_fixture: "quiet-room",
    });
  });

  it.each(["warm", "connecting", "cold"] as const)(
    "preserves the explicit %s connection cohort",
    (cohort) => {
      const [result] = reduceJarvisRealtimeTurns([
        event("wake_audio_stream_started", 50, { detail: { cohort }, id: `start-${cohort}` }),
        event("provider.user_input_committed", 100, { item_id: "item-1", id: `commit-${cohort}` }),
        event("response.done", 200, {
          item_id: "item-1",
          detail: { status: "completed" },
          id: `done-${cohort}`,
        }),
      ]);
      expect(result.connection_cohort).toBe(cohort);
    },
  );
});
