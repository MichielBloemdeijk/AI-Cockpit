import type { CouncilResponse, UsageStats } from "../api/conversations";

export interface ChatActionError {
  scope: "send" | "archive" | "branch";
  message: string;
}

export interface ChatMessage {
  id: string;
  activityId?: string;
  runId?: string | null;
  role: "user" | "assistant" | "system";
  /** Chat and Jarvis messages are "message"; the other kinds are task-trace rows. */
  kind?: "message" | "status" | "question" | "answer" | "summary" | "error";
  label?: string;
  title?: string;
  content: string;
  createdAt?: string | null;
  tone?: "default" | "info" | "success" | "warning" | "error";
  badges?: string[];
  branchable?: boolean;
  branchBlockReason?: string;
  councilData?: CouncilResponse;
  usage?: UsageStats;
  streaming?: boolean;
  status?: string | null;
  taskId?: string | null;
}

export interface AgentStatus {
  title: string;
  content: string;
  tone: "info" | "success" | "warning" | "error";
  runId?: string | null;
  active: boolean;
}
