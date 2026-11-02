/** Reviewed memory and the knowledge documents it publishes. */
import { apiRequest } from "./http";

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

export function extractConversationMemoryItems(conversationId: string): Promise<MemoryItemView[]> {
  return apiRequest(`/api/conversations/${encodeURIComponent(conversationId)}/memory-items/extract`, { method: "POST", body: {} });
}

export function listKnowledgeReviewItems(status = "proposed"): Promise<{ items: KnowledgeReviewItemView[] }> {
  return apiRequest(`/api/knowledge/review-items?status=${encodeURIComponent(status)}`);
}

export function listKnowledgeDocuments(): Promise<{ documents: KnowledgeDocumentView[] }> {
  return apiRequest("/api/knowledge/documents");
}

export function approveKnowledgeItem(memoryItemId: string): Promise<{ memory_item: MemoryItemView; knowledge_path: string }> {
  return apiRequest(`/api/knowledge/memory-items/${encodeURIComponent(memoryItemId)}/approve`, { method: "POST" });
}

export function rejectKnowledgeItem(memoryItemId: string): Promise<MemoryItemView> {
  return apiRequest(`/api/knowledge/memory-items/${encodeURIComponent(memoryItemId)}/reject`, { method: "POST" });
}

export function deleteKnowledgeItem(memoryItemId: string): Promise<MemoryItemView> {
  return apiRequest(`/api/knowledge/memory-items/${encodeURIComponent(memoryItemId)}`, { method: "DELETE" });
}
