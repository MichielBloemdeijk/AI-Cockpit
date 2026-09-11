export type JarvisTurnOutcome = "completed" | "cancelled" | "timed_out" | "failed" | "rejected";
export type JarvisPlaybackState = "observed" | "not_observed" | "not_applicable";
export type JarvisTranscriptState =
  | "available_before_response"
  | "available_late"
  | "missing";
export type JarvisGateState = "accepted" | "rejected" | "timed_out" | "not_applicable";
export type JarvisConnectionCohort = "warm" | "connecting" | "cold";

export interface JarvisRealtimeTurnResult {
  schema: "jarvis.realtime_turn_result.v3";
  turn_key: string;
  activation_id: string | null;
  session_id: string | null;
  generation: number | null;
  input_item_id: string | null;
  request_id: string | null;
  response_id: string | null;
  generation_outcome: JarvisTurnOutcome;
  playback_state: JarvisPlaybackState;
  transcript_state: JarvisTranscriptState;
  gate_state: JarvisGateState;
  connection_cohort: JarvisConnectionCohort;
  configuration_id: string;
  latency_ms: Record<string, number>;
  clock_uncertainty_ms: number | null;
  failure_code: string | null;
  benchmark_run_id: string | null;
  benchmark_fixture: string | null;
}

export interface JarvisRealtimeTurnsSummary {
  schema: "jarvis.realtime_turns.v3";
  turns: JarvisRealtimeTurnResult[];
}

export type JarvisRealtimeReducerEntry = Record<string, unknown> & {
  kind?: string;
  event_type?: string;
  source?: string;
  monotonic_ms?: number;
  wall_time?: string;
  activation_id?: string;
  session_id?: string;
  generation?: number;
  item_id?: string;
  input_item_id?: string;
  request_id?: string;
  response_id?: string;
  provider_input_item_id?: string;
  configuration_id?: string;
  cohort?: string;
  client_sequence?: number;
  id?: string;
  sequence?: number;
  timing_ms?: Record<string, unknown>;
  detail?: string | Record<string, unknown>;
  event_id?: string;
  clock_uncertainty_ms?: number;
  benchmark_run_id?: string;
  benchmark_fixture?: string;
};

interface TurnState {
  activationId: string | null;
  sessionId: string | null;
  generation: number | null;
  itemId: string | null;
  requestId: string | null;
  responseId: string | null;
  gate: JarvisGateState;
  outcome: JarvisTurnOutcome | null;
  playback: JarvisPlaybackState;
  transcript: JarvisTranscriptState;
  cohort: JarvisConnectionCohort;
  configurationId: string;
  failureCode: string | null;
  uncertainty: number | null;
  times: Record<string, number>;
  responseCreatedAt: number | null;
  benchmarkRunId: string | null;
  benchmarkFixture: string | null;
}

function detailOf(entry: JarvisRealtimeReducerEntry): Record<string, unknown> {
  if (entry.detail && typeof entry.detail === "object") return entry.detail;
  if (typeof entry.detail !== "string") return {};
  try {
    const parsed: unknown = JSON.parse(entry.detail);
    return parsed && typeof parsed === "object" ? parsed as Record<string, unknown> : {};
  } catch {
    return {};
  }
}

function numberOf(value: unknown): number | null {
  return typeof value === "number" && Number.isFinite(value) ? value : null;
}

function stringOf(value: unknown): string | null {
  return typeof value === "string" && value.length ? value : null;
}

function canonicalKind(entry: JarvisRealtimeReducerEntry): string {
  const kind = String(entry.kind ?? entry.event_type ?? "");
  return kind.replace(/^backend\./, "");
}

function eventIdentity(entry: JarvisRealtimeReducerEntry, index: number): string {
  const scope = `${entry.session_id ?? ""}:${entry.generation ?? ""}`;
  if (entry.client_sequence !== undefined) return `client:${scope}:${entry.client_sequence}`;
  if (entry.event_id) return `event:${scope}:${entry.event_id}`;
  if (entry.id) return `durable:${scope}:${entry.id}`;
  return `${entry.source ?? "event"}:${scope}:${entry.kind ?? entry.event_type ?? "event"}:${entry.sequence ?? index}`;
}

function addTime(state: TurnState, name: string, value: number | null): void {
  if (value === null || !Number.isFinite(value)) return;
  state.times[name] = value;
}

function deriveLatency(state: TurnState): Record<string, number> {
  const result: Record<string, number> = {};
  const intervals: Array<[string, string, string]> = [
    ["local_speech_end_to_provider_commit", "speech_end", "commit"],
    ["provider_commit_to_gate_ready", "commit", "gate_ready"],
    ["gate_ready_to_response_create_sent", "gate_ready", "response_create_sent"],
    ["response_create_sent_to_response_created", "response_create_sent", "response_created"],
    ["response_created_to_first_provider_audio", "response_created", "first_provider_audio"],
    ["first_provider_audio_to_playback_proxy", "first_provider_audio", "playback"],
  ];
  for (const [name, start, end] of intervals) {
    const first = state.times[start];
    const second = state.times[end];
    if (typeof first !== "number" || typeof second !== "number") continue;
    result[name] = Math.max(0, second - first);
  }
  return result;
}

function findState(
  states: TurnState[],
  entry: JarvisRealtimeReducerEntry,
  detail: Record<string, unknown>,
): TurnState | undefined {
  const activationId = stringOf(entry.activation_id) ?? stringOf(detail.activation_id);
  const itemId = stringOf(entry.item_id)
    ?? stringOf(entry.input_item_id)
    ?? stringOf(entry.provider_input_item_id)
    ?? stringOf(detail.item_id)
    ?? stringOf(detail.input_item_id);
  const requestId = stringOf(entry.request_id) ?? stringOf(detail.request_id);
  const responseId = stringOf(entry.response_id) ?? stringOf(detail.response_id);
  const byItem = itemId && states.find((state) => state.itemId === itemId);
  if (byItem) return byItem;
  const byRequest = requestId && states.find((state) => state.requestId === requestId);
  if (byRequest) return byRequest;
  const byResponse = responseId && states.find((state) => state.responseId === responseId);
  if (byResponse) return byResponse;
  if (!activationId) return undefined;
  // Activation IDs remain attached to the first wake turn only. A later
  // provider input item is a follow-up and must get its own terminal record.
  return states.find((state) => (
    state.activationId === activationId
    && (state.itemId === null || itemId === null || state.itemId === itemId)
    && (state.outcome === null || (!itemId && !requestId && !responseId))
  ));
}

function newState(entry: JarvisRealtimeReducerEntry, detail: Record<string, unknown>): TurnState {
  return {
    activationId: stringOf(entry.activation_id) ?? stringOf(detail.activation_id),
    sessionId: stringOf(entry.session_id) ?? stringOf(detail.session_id),
    generation: numberOf(entry.generation) ?? numberOf(detail.generation),
    itemId: stringOf(entry.item_id)
      ?? stringOf(entry.input_item_id)
      ?? stringOf(entry.provider_input_item_id)
      ?? stringOf(detail.item_id)
      ?? stringOf(detail.input_item_id),
    requestId: stringOf(entry.request_id) ?? stringOf(detail.request_id),
    responseId: stringOf(entry.response_id) ?? stringOf(detail.response_id),
    gate: "not_applicable",
    outcome: null,
    playback: "not_applicable",
    transcript: "missing",
    cohort: "cold",
    configurationId: stringOf(entry.configuration_id) ?? stringOf(detail.configuration_id) ?? "unknown",
    failureCode: null,
    uncertainty: numberOf(detail.clock_uncertainty_ms),
    times: {},
    responseCreatedAt: null,
    benchmarkRunId: stringOf(entry.benchmark_run_id) ?? stringOf(detail.benchmark_run_id),
    benchmarkFixture: stringOf(entry.benchmark_fixture) ?? stringOf(detail.benchmark_fixture),
  };
}

function mergeCompatibleStates(states: TurnState[], target: TurnState): TurnState {
  const identityKeys = ["activationId", "itemId", "requestId", "responseId"] as const;
  for (let index = states.length - 1; index >= 0; index -= 1) {
    const other = states[index];
    if (other === target) continue;
    const sharesIdentity = identityKeys.some((key) => target[key] && target[key] === other[key]);
    const conflicts = identityKeys.some((key) => target[key] && other[key] && target[key] !== other[key])
      || Boolean(target.sessionId && other.sessionId && target.sessionId !== other.sessionId)
      || Boolean(target.generation !== null && other.generation !== null && target.generation !== other.generation);
    if (!sharesIdentity || conflicts) continue;

    for (const key of identityKeys) target[key] = target[key] ?? other[key];
    target.sessionId = target.sessionId ?? other.sessionId;
    target.generation = target.generation ?? other.generation;
    if (target.gate === "not_applicable") target.gate = other.gate;
    target.outcome = target.outcome ?? other.outcome;
    if (other.playback === "observed") target.playback = "observed";
    else if (target.playback === "not_applicable") target.playback = other.playback;
    if (other.transcript === "available_before_response") target.transcript = other.transcript;
    else if (target.transcript === "missing") target.transcript = other.transcript;
    if (target.cohort === "cold" && other.cohort !== "cold") target.cohort = other.cohort;
    if (target.configurationId === "unknown") target.configurationId = other.configurationId;
    target.failureCode = target.failureCode ?? other.failureCode;
    target.uncertainty = target.uncertainty ?? other.uncertainty;
    target.responseCreatedAt = target.responseCreatedAt ?? other.responseCreatedAt;
    target.benchmarkRunId = target.benchmarkRunId ?? other.benchmarkRunId;
    target.benchmarkFixture = target.benchmarkFixture ?? other.benchmarkFixture;
    for (const [name, value] of Object.entries(other.times)) {
      target.times[name] = target.times[name] === undefined
        ? value
        : Math.min(target.times[name], value);
    }
    states.splice(index, 1);
  }
  return target;
}

/** Reduce browser/backend/provider traces by IDs rather than arrival order. */
export function reduceJarvisRealtimeTurns(
  entries: readonly JarvisRealtimeReducerEntry[],
): JarvisRealtimeTurnResult[] {
  const states: TurnState[] = [];
  const seen = new Set<string>();
  for (const [index, entry] of entries.entries()) {
    const detail = detailOf(entry);
    const hasTurnIdentity = Boolean(
      stringOf(entry.activation_id)
      || stringOf(entry.item_id)
      || stringOf(entry.input_item_id)
      || stringOf(entry.provider_input_item_id)
      || stringOf(entry.request_id)
      || stringOf(entry.response_id)
      || stringOf(detail.activation_id)
      || stringOf(detail.item_id)
      || stringOf(detail.input_item_id)
      || stringOf(detail.provider_input_item_id)
      || stringOf(detail.request_id)
      || stringOf(detail.response_id)
    );
    // The compatibility v2 trace contains connection/setup milestones with
    // no durable identity. They are not a v3 turn and must not become a
    // synthetic failed activation.
    if (!hasTurnIdentity) continue;
    const identity = eventIdentity(entry, index);
    if (seen.has(identity)) continue;
    seen.add(identity);
    const kind = canonicalKind(entry);
    let state = findState(states, entry, detail);
    if (!state) {
      state = newState(entry, detail);
      states.push(state);
    }
    const at = numberOf(entry.monotonic_ms);
    const itemId = stringOf(entry.item_id)
      ?? stringOf(entry.input_item_id)
      ?? stringOf(entry.provider_input_item_id)
      ?? stringOf(detail.item_id)
      ?? stringOf(detail.input_item_id);
    const requestId = stringOf(entry.request_id) ?? stringOf(detail.request_id);
    const responseId = stringOf(entry.response_id) ?? stringOf(detail.response_id);
    const activationId = stringOf(entry.activation_id) ?? stringOf(detail.activation_id);
    const sessionId = stringOf(entry.session_id) ?? stringOf(detail.session_id);
    const generation = numberOf(entry.generation) ?? numberOf(detail.generation);
    if (activationId && !state.activationId) state.activationId = activationId;
    if (sessionId && !state.sessionId) state.sessionId = sessionId;
    if (generation !== null && state.generation === null) state.generation = generation;
    if (itemId) state.itemId = state.itemId ?? itemId;
    if (requestId) state.requestId = state.requestId ?? requestId;
    if (responseId) state.responseId = state.responseId ?? responseId;
    state = mergeCompatibleStates(states, state);
    const configurationId = stringOf(entry.configuration_id) ?? stringOf(detail.configuration_id);
    if (configurationId) state.configurationId = configurationId;
    const uncertainty = numberOf(detail.clock_uncertainty_ms) ?? numberOf(entry.clock_uncertainty_ms);
    if (uncertainty !== null) state.uncertainty = uncertainty;
    state.benchmarkRunId = state.benchmarkRunId
      ?? stringOf(entry.benchmark_run_id)
      ?? stringOf(detail.benchmark_run_id);
    state.benchmarkFixture = state.benchmarkFixture
      ?? stringOf(entry.benchmark_fixture)
      ?? stringOf(detail.benchmark_fixture);
    if (at !== null) {
      if (kind === "local_speech_stopped" || kind === "wake_local_speech_stopped" || kind === "wake_local_endpoint") {
        addTime(state, "speech_end", at);
      } else if (kind.includes("committed") || kind.includes("user_input_committed")) {
        addTime(state, "commit", at);
      } else if (kind.includes("gate.started")) {
        addTime(state, "gate_started", at);
      } else if (kind.includes("gate.accepted") || kind.includes("gate.rejected") || kind.includes("gate.timed_out")) {
        addTime(state, "gate_decision", at);
        addTime(state, "gate_ready", at);
      } else if (
        kind.includes("response.create.sent")
        || kind.includes("response_create.sent")
        || kind === "response.create"
        || kind.includes("response.arbiter.started")
      ) {
        addTime(state, "response_create_sent", at);
      } else if (kind.includes("response.created") || kind === "provider.assistant_generating" || kind === "assistant_generating") {
        addTime(state, "response_created", at);
        state.responseCreatedAt = state.responseCreatedAt ?? at;
      } else if (kind.includes("response.output_audio.delta") || kind.includes("assistant_speaking") || kind.includes("output_audio.started")) {
        addTime(state, "first_provider_audio", at);
      } else if (kind === "first_playable_media" || kind === "audio_element_playing" || kind.includes("playback")) {
        addTime(state, "playback", at);
      }
    }
    if (kind.includes("gate.accepted")) state.gate = "accepted";
    if (kind.includes("gate.rejected")) {
      state.gate = "rejected";
      state.outcome = "rejected";
    }
    if (kind.includes("gate.timed_out")) {
      state.gate = "timed_out";
      state.outcome = state.outcome ?? "timed_out";
    }
    if (kind.includes("provider.user_transcript") || kind.endsWith("user_transcript") || kind === "user_transcript") {
      state.transcript = state.responseCreatedAt === null ? "available_before_response" : "available_late";
    }
    const responseStatus = stringOf(detail.status) ?? stringOf(detail.response_status);
    if (kind.includes("committed") && state.gate === "accepted") {
      // Immediate mode is gate-ready before the provider commits. The commit
      // is still the start of the response budget, so its gate interval is 0.
      addTime(state, "gate_ready", state.times.commit ?? at);
    }
    const responseCancelled = (
      kind.includes("assistant_interrupted")
      || kind.includes("response.cancel")
      || responseStatus === "cancelled"
      || responseStatus === "canceled"
      || responseStatus === "incomplete"
    );
    const responseFailed = responseStatus === "failed";
    const responseCompleted = responseStatus === "completed"
      || kind.includes("assistant_response")
      || ((kind === "response.done" || kind === "provider.response.done") && responseStatus === null);
    if (responseFailed) {
      state.outcome = "failed";
      state.failureCode = state.failureCode ?? stringOf(detail.error_code) ?? "response_failed";
      state.playback = state.playback === "not_applicable" ? "not_observed" : state.playback;
    } else if (responseCancelled) {
      state.outcome = "cancelled";
      state.playback = state.playback === "not_applicable" ? "not_observed" : state.playback;
    } else if (responseCompleted) {
      state.outcome = state.outcome ?? "completed";
      state.playback = state.playback === "not_applicable" ? "not_observed" : state.playback;
    }
    if (kind.includes("timeout") || kind.includes("timed_out")) {
      state.outcome = state.outcome ?? "timed_out";
      state.failureCode = state.failureCode ?? stringOf(detail.error_code) ?? "response_timeout";
    }
    if (kind.includes("provider_error") || kind.includes("connection.attempt.failed") || kind.includes("provider_protocol_warning")) {
      if (kind.includes("provider_protocol_warning")) {
        // A verified empty commit is diagnostic metadata, not a failed turn.
        state.failureCode = state.failureCode ?? stringOf(detail.original_code) ?? "provider_protocol_warning";
      } else {
        state.outcome = state.outcome ?? "failed";
        state.failureCode = stringOf(entry.detail) ?? stringOf(detail.error_code) ?? "provider_error";
      }
    }
    if (kind === "first_playable_media" || kind === "audio_element_playing") state.playback = "observed";
    const cohort = stringOf(entry.cohort) ?? stringOf(detail.cohort);
    if (cohort === "warm" || cohort === "connecting" || cohort === "cold") state.cohort = cohort;
    if (kind.includes("standby_ready")) state.cohort = "warm";
    if (kind.includes("connection_attempt") || kind.includes("connecting")) {
      state.cohort = state.cohort === "warm" ? "warm" : "connecting";
    }
    const timing = entry.timing_ms;
    if (timing && typeof timing === "object") {
      for (const [key, value] of Object.entries(timing)) {
        const numeric = numberOf(value);
        if (numeric !== null && key === "clock_uncertainty") state.uncertainty = numeric;
      }
    }
  }

  return states.map((state, index) => {
    if (!state.outcome) {
      state.outcome = state.gate === "timed_out"
        ? "timed_out"
        : state.gate === "rejected" ? "rejected" : "failed";
      state.failureCode = state.failureCode ?? "missing_terminal_event";
    }
    if (state.outcome === "rejected") state.playback = "not_applicable";
    if (state.outcome !== "completed" && state.outcome !== "cancelled" && state.playback === "not_applicable") {
      state.playback = "not_observed";
    }
    const turnKey = state.activationId
      ?? `${state.sessionId ?? "unknown"}:${state.generation ?? "unknown"}:${state.itemId ?? index}`;
    return {
      schema: "jarvis.realtime_turn_result.v3",
      turn_key: turnKey,
      activation_id: state.activationId,
      session_id: state.sessionId,
      generation: state.generation,
      input_item_id: state.itemId,
      request_id: state.requestId,
      response_id: state.responseId,
      generation_outcome: state.outcome,
      playback_state: state.playback,
      transcript_state: state.transcript,
      gate_state: state.gate,
      connection_cohort: state.cohort,
      configuration_id: state.configurationId,
      latency_ms: deriveLatency(state),
      clock_uncertainty_ms: state.uncertainty,
      failure_code: state.failureCode,
      benchmark_run_id: state.benchmarkRunId,
      benchmark_fixture: state.benchmarkFixture,
    } satisfies JarvisRealtimeTurnResult;
  });
}

export function summarizeJarvisRealtimeTurns(
  entries: readonly JarvisRealtimeReducerEntry[],
): JarvisRealtimeTurnsSummary {
  return {
    schema: "jarvis.realtime_turns.v3",
    turns: reduceJarvisRealtimeTurns(entries),
  };
}
