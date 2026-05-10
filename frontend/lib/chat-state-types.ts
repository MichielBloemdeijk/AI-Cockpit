import type { CouncilResponse, UsageStats } from "./api";

export interface ChatActionError {
  scope: "send" | "tool" | "archive" | "branch";
  message: string;
}

export interface ChatMessage {
  id: string;
  runId?: string | null;
  step?: number;
  role: "user" | "assistant" | "system";
  kind?: "message" | "status" | "plan" | "thought" | "tool_call" | "tool_result" | "question" | "answer" | "summary" | "error";
  label?: string;
  title?: string;
  content: string;
  createdAt?: string | null;
  tone?: "default" | "info" | "success" | "warning" | "error";
  badges?: string[];
  code?: string;
  sections?: Array<{ title: string; items: string[] }>;
  branchable?: boolean;
  branchBlockReason?: string;
  councilData?: CouncilResponse;
  usage?: UsageStats;
  streaming?: boolean;
}

export interface AgentStatus {
  title: string;
  content: string;
  tone: "info" | "success" | "warning" | "error";
  runId?: string | null;
  active: boolean;
}