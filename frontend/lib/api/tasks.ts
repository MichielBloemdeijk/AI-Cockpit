/** First-class tasks: dispatch, detail, controls, questions, the worker pool and worker tools. */
import { createUuid } from "../uuid";
import { apiRequest } from "./http";

export interface TaskSummary {
  id: string;
  result_url?: string | null;
  origin_surface: string;
  origin_conversation_id: string | null;
  origin_turn_id: string | null;
  goal: string;
  status: "queued" | "waiting_for_worker" | "running" | "waiting_for_input" | "waiting_until" | "paused" | "completed" | "failed" | "interrupted" | "cancelled";
  execution_kind: "agent" | "workflow";
  status_reason: string | null;
  creation_order: number;
  current_attempt_id: string | null;
  current_run_id: string | null;
  pending_control_id: string | null;
  goal_revision: number;
  control_revision: number;
  workspace_generation: number;
  queue_entered_at: string;
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
  /** A retry must carry the owner's reason and accept the changed shared resources. */
  retry_requires_change_acknowledgement: boolean;
}

export interface TaskAttempt {
  id: string;
  attempt_number: number;
  execution_run_id: string | null;
  supervisor_generation: number | null;
  parent_attempt_id: string | null;
  resume_reason: string | null;
  worker_id: string | null;
  workspace_path: string | null;
  checkpoint: Record<string, unknown> | null;
  checkpoint_version: number;
  claimed_at: string | null;
  heartbeat_at: string | null;
  last_progress_at: string | null;
  active_operation: string | null;
  operation_started_at: string | null;
  operation_deadline: string | null;
  recovery_metadata: Record<string, unknown> | null;
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
  execution_run_id: string | null;
  native_item_id: string | null;
  event_type: string;
  payload: Record<string, unknown> | null;
  causality: Record<string, unknown> | null;
  created_at: string;
}

export interface TaskQuestion {
  id: string;
  asking_run_id: string | null;
  resolution_revision: number | null;
  asked_attempt_id: string | null;
  answered_attempt_id: string | null;
  source_task_event_id: string | null;
  question: string;
  permission?: { connector: string; account: string; tool: string; arguments: Record<string, unknown> } | null;
  answer: string | null;
  state: string;
  asked_at: string;
  answered_at: string | null;
}

export type AttentionState = "pending" | "handled" | "superseded";

export interface TaskArtifact {
  id: string;
  attempt_id: string | null;
  execution_run_id: string | null;
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
  state: AttentionState;
  source_task_event_id: string | null;
  created_at: string;
  handled_at: string | null;
  handled_by: string | null;
  /** @deprecated Use handled_at. */
  presented_at: string | null;
}

export interface TaskDetail extends TaskSummary {
  results?: TaskResultLink[];
  attempts: TaskAttempt[];
  turns: TaskTurn[];
  runs: TaskExecutionRun[];
  controls: TaskControl[];
  operations: TaskOperation[];
  events: TaskEvent[];
  questions: TaskQuestion[];
  artifacts: TaskArtifact[];
  targets: TaskTarget[];
  presentations: TaskPresentation[];
  worker_health: { status: string; worker_id: string | null; heartbeat_at: string | null };
  /** A follow-up may ask for an answer only, without changing a result. */
  follow_up_answer_supported: boolean;
}

/** A task's result: a record in one of the user's apps, opened at `url`. */
export interface TaskResultLink {
  result_id: string; url: string; app: string; collection: string; key: string; kind: string; title: string;
}

export type TaskResultIntent = { mode: "new" } |
  { mode: "update"; result_id: string; expected_revision?: number } |
  { mode: "answer"; result_id?: string };

export interface TaskTurn {
  id: string;
  sequence: number;
  role: string;
  content: string | null;
  origin: string;
  execution_run_id: string | null;
  created_at: string;
}

export interface TaskExecutionRun {
  id: string;
  ordinal: number;
  status: string;
  execution_kind: "agent" | "workflow";
  configuration?: { tools?: ToolOverrides; workflow?: { capabilities?: string[] } };
  current_attempt_id: string | null;
  terminal_summary: string | null;
  started_at: string | null;
  finished_at: string | null;
}

export interface TaskControl {
  id: string;
  action: string;
  state: string;
  requester: string;
  reason: string | null;
  requested_at: string;
  confirmed_at: string | null;
}

export interface TaskOperation {
  id: string;
  operation_type: string;
  state: string;
  started_at: string;
  completed_at: string | null;
}

export interface TaskPool {
  enabled: boolean;
  supervisor_generation: number | null;
  supervisor_heartbeat_at: string | null;
  busy: number;
  standby: number;
  starting: number;
  stale: number;
  queue_depth: number;
  waiting_for_worker: number;
  max_busy: number;
  saturated: boolean;
  workers: Array<{ id: string; state: string; assignment_attempt_id: string | null; heartbeat_at: string }>;
}

export type TaskPoolAction = "repair_capacity" | "replace_worker" | "restart_pool";

export interface TaskDispatch {
  goal: string;
  idempotency_key: string;
  origin_surface?: string;
  origin_conversation_id?: string;
  origin_turn_id?: string;
  importance?: string;
  presentation_policy?: string;
  result?: TaskResultIntent;
  execution_config?: { tools: ToolOverrides };
  targets?: Array<{ resource_type: string; resource_key: string; mutable?: boolean }>;
}

export type NativeToolGroup = "native.local" | "native.research" | "native.subagents" | "native.skills";

export interface ToolOverrides { add: NativeToolGroup[]; remove: NativeToolGroup[] }

export interface WorkerToolPolicy {
  defaults: NativeToolGroup[];
  disabled: NativeToolGroup[];
  command_network: boolean;
  approved_skills: string[];
}

export interface WorkerToolSettings {
  revision: number;
  policy: WorkerToolPolicy;
  groups: NativeToolGroup[];
  local_execution_mode: "native" | "trusted";
  connectors: { connector: string; tool: string; description: string }[];
}

export interface TaskEventReplayResponse {
  latest_sequence: number;
  events: TaskEvent[];
}

export function listTasks(statuses: string[] = []): Promise<TaskSummary[]> {
  const params = new URLSearchParams();
  statuses.forEach((status) => params.append("status", status));
  return apiRequest(`/api/tasks${params.size ? `?${params}` : ""}`);
}

export function getTask(taskId: string): Promise<TaskDetail> {
  return apiRequest(`/api/tasks/${encodeURIComponent(taskId)}`);
}

export function getTaskEvents(after = 0): Promise<TaskEventReplayResponse> {
  return apiRequest(`/api/tasks/events?${new URLSearchParams({ after: String(after) })}`);
}

export function getTaskWorkerHealth(): Promise<{ status: string; worker_id: string | null; heartbeat_at: string | null }> {
  return apiRequest("/api/tasks/worker");
}

export function getTaskPool(): Promise<TaskPool> {
  return apiRequest("/api/tasks/pool");
}

export function controlTaskPool(
  action: TaskPoolAction,
  reason: string,
  workerId?: string,
): Promise<{ id: string; state: string; affected_worker_ids: string[] }> {
  return apiRequest("/api/tasks/pool/control", {
    method: "POST",
    body: { action, idempotency_key: createUuid(), reason, worker_id: workerId ?? null },
  });
}

export function dispatchTask(request: TaskDispatch): Promise<TaskSummary> {
  return apiRequest("/api/tasks", { method: "POST", body: request });
}

export function continueTask(
  taskId: string,
  content: string,
  result?: TaskResultIntent,
  executionConfig?: { tools: ToolOverrides },
): Promise<{ run_id: string; status: string; result_url?: string | null }> {
  return apiRequest(`/api/tasks/${encodeURIComponent(taskId)}/turns`, {
    method: "POST",
    body: { content, idempotency_key: createUuid(), ...(result ? { result } : {}), ...(executionConfig ? { execution_config: executionConfig } : {}) },
  });
}

export function controlTask(
  taskId: string,
  action: string,
  parameters: Record<string, unknown> = {},
  expectedRunId?: string | null,
  expectedControlRevision?: number,
  reason?: string,
): Promise<{ task: TaskSummary; command: TaskControl }> {
  return apiRequest(`/api/tasks/${encodeURIComponent(taskId)}/control`, {
    method: "POST",
    body: {
      action,
      parameters,
      idempotency_key: createUuid(),
      expected_run_id: expectedRunId,
      expected_control_revision: expectedControlRevision,
      reason,
    },
  });
}

export function answerTaskQuestion(taskId: string, questionId: string, answer: string): Promise<TaskSummary> {
  return apiRequest(`/api/tasks/${encodeURIComponent(taskId)}/questions/${encodeURIComponent(questionId)}/answer`, {
    method: "POST", body: { answer, idempotency_key: createUuid() },
  });
}

export function getWorkerToolSettings(): Promise<WorkerToolSettings> {
  return apiRequest("/api/worker-tools");
}

export function saveWorkerToolSettings(settings: WorkerToolSettings): Promise<{ revision: number; policy: WorkerToolPolicy }> {
  return apiRequest("/api/worker-tools", {
    method: "PUT", body: { expected_revision: settings.revision, policy: settings.policy },
  });
}
