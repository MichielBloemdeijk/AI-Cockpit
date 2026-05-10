"use client";

import { Archive, Clock3, FileStack, FolderOpen, GitBranch, Layers3 } from "lucide-react";
import clsx from "clsx";

import type { ConversationArtifactView, ConversationDetail, ConversationEventView } from "../../lib/api";
import { summarizeAgentEvent } from "../../lib/agent-event-presenter";
import { formatTokenCount, formatUsageCost, summarizeConversationUsage } from "../../lib/usage";

import { formatConversationLabel, formatTraceLabel } from "./formatters";

interface Props {
  activeConversation: ConversationDetail | null;
  activeConversationId: string | null;
  activeBranchKey: string;
  events: ConversationEventView[];
  artifacts: ConversationArtifactView[];
  mobile?: boolean;
  showArchived: boolean;
  onArchiveConversation: () => void;
  onSelectBranch: (branchKey: string) => void;
  onToggleShowArchived: () => void;
}

export function ConversationDetailsPanel({
  activeConversation,
  activeConversationId,
  activeBranchKey,
  events,
  artifacts,
  mobile = false,
  showArchived,
  onArchiveConversation,
  onSelectBranch,
  onToggleShowArchived,
}: Props) {
  const usageSummary = summarizeConversationUsage(events, artifacts);
  const sessionCostLabel = formatUsageCost(usageSummary.totalUsage?.cost);
  const sessionTokenLabel = formatTokenCount(usageSummary.totalUsage?.total_tokens);
  const hasUsageSummary = usageSummary.requestCount > 0;
  const hasTraceOrArtifacts = activeConversationId && (events.length > 0 || artifacts.length > 0);

  if (mobile) {
    return (
      <div className="flex-1 space-y-4 overflow-y-auto p-4">
        {activeConversation && activeConversation.branches.length > 1 && (
          <section className="rounded-2xl border border-zinc-800 bg-zinc-900/80 p-4">
            <div className="flex items-center gap-2 text-sm font-medium text-zinc-100">
              <GitBranch size={15} className="text-zinc-500" />
              Branches
            </div>
            <div className="mt-3 flex flex-wrap gap-2">
              <button
                onClick={onToggleShowArchived}
                className="rounded-full border border-zinc-700 bg-zinc-950 px-3 py-1 text-xs text-zinc-300"
              >
                {showArchived ? "Hide archived" : "Show archived"}
              </button>
              {activeConversationId && (
                <button
                  onClick={onArchiveConversation}
                  className="rounded-full border border-zinc-700 bg-zinc-950 px-3 py-1 text-xs text-zinc-300"
                >
                  Archive chat
                </button>
              )}
              {activeConversation.branches.map((branch) => (
                <button
                  key={branch.branch_key}
                  onClick={() => onSelectBranch(branch.branch_key)}
                  className={clsx(
                    "rounded-full border px-3 py-1 text-xs transition-colors",
                    activeBranchKey === branch.branch_key
                      ? "border-blue-500/40 bg-blue-500/10 text-blue-300"
                      : "border-zinc-700 bg-zinc-950 text-zinc-400"
                  )}
                >
                  {branch.label || branch.branch_key}
                </button>
              ))}
            </div>
          </section>
        )}

        {activeConversationId && hasUsageSummary && (
          <section className="rounded-2xl border border-zinc-800 bg-zinc-900/80 p-4">
            <div className="flex items-center gap-2 text-sm font-medium text-zinc-100">
              <Layers3 size={15} className="text-zinc-500" />
              Usage
            </div>
            <div className="mt-3 grid grid-cols-2 gap-2">
              <div className="rounded-xl border border-zinc-800 bg-zinc-950/70 px-3 py-2">
                <div className="text-[11px] uppercase tracking-wide text-zinc-500">Session total</div>
                <div className="mt-1 text-sm text-zinc-100">{sessionCostLabel ? `${sessionCostLabel} credits` : "Unavailable"}</div>
              </div>
              <div className="rounded-xl border border-zinc-800 bg-zinc-950/70 px-3 py-2">
                <div className="text-[11px] uppercase tracking-wide text-zinc-500">Requests</div>
                <div className="mt-1 text-sm text-zinc-100">{usageSummary.requestCount}</div>
              </div>
              <div className="col-span-2 rounded-xl border border-zinc-800 bg-zinc-950/70 px-3 py-2">
                <div className="text-[11px] uppercase tracking-wide text-zinc-500">Tokens</div>
                <div className="mt-1 text-sm text-zinc-100">{sessionTokenLabel ?? "Unavailable"}</div>
              </div>
            </div>
          </section>
        )}

        {hasTraceOrArtifacts && (
          <>
            <TraceSection events={events} />
            <WorkspaceSection
              activeConversation={activeConversation}
              artifacts={artifacts}
              hasUsageSummary={hasUsageSummary}
              requestCount={usageSummary.requestCount}
              sessionCostLabel={sessionCostLabel}
            />
          </>
        )}
      </div>
    );
  }

  if (!activeConversationId || !hasTraceOrArtifacts) {
    return null;
  }

  return (
    <div className="mb-4 hidden gap-4 px-4 lg:grid lg:grid-cols-[1.1fr_0.9fr]">
      <TraceSection events={events} />
      <WorkspaceSection
        activeConversation={activeConversation}
        artifacts={artifacts}
        hasUsageSummary={hasUsageSummary}
        requestCount={usageSummary.requestCount}
        sessionCostLabel={sessionCostLabel}
      />
    </div>
  );
}

function TraceSection({ events }: { events: ConversationEventView[] }) {
  return (
    <section className="rounded-2xl border border-zinc-800 bg-zinc-900/80 p-4">
      <div className="flex items-center gap-2 text-sm font-medium text-zinc-100">
        <Clock3 size={15} className="text-zinc-500" />
        Recent Trace
      </div>
      <div className="mt-3 space-y-2">
        {events.slice(-6).reverse().map((event) => (
          <div key={event.id} className="rounded-xl border border-zinc-800 bg-zinc-950/70 px-3 py-2">
            <div className="flex items-center justify-between gap-3">
              <div className="text-xs text-zinc-200">{formatTraceLabel(event.event_type)}</div>
              <div className="text-[11px] text-zinc-500">{formatConversationLabel(event.created_at)}</div>
            </div>
            {summarizeAgentEvent(event.event_type, event.payload_json) && (
              <div className="mt-1 line-clamp-2 text-xs text-zinc-500">
                {summarizeAgentEvent(event.event_type, event.payload_json)}
              </div>
            )}
          </div>
        ))}
      </div>
    </section>
  );
}

function WorkspaceSection({
  activeConversation,
  artifacts,
  hasUsageSummary,
  requestCount,
  sessionCostLabel,
}: {
  activeConversation: ConversationDetail | null;
  artifacts: ConversationArtifactView[];
  hasUsageSummary: boolean;
  requestCount: number;
  sessionCostLabel: string | null;
}) {
  return (
    <section className="rounded-2xl border border-zinc-800 bg-zinc-900/80 p-4">
      <div className="flex items-center gap-2 text-sm font-medium text-zinc-100">
        <FolderOpen size={15} className="text-zinc-500" />
        Workspace
      </div>
      {hasUsageSummary && (
        <div className="mt-3 grid grid-cols-2 gap-2">
          <div className="rounded-xl border border-zinc-800 bg-zinc-950/70 px-3 py-2">
            <div className="text-[11px] uppercase tracking-wide text-zinc-500">Session total</div>
            <div className="mt-1 text-sm text-zinc-100">{sessionCostLabel ? `${sessionCostLabel} credits` : "Unavailable"}</div>
          </div>
          <div className="rounded-xl border border-zinc-800 bg-zinc-950/70 px-3 py-2">
            <div className="text-[11px] uppercase tracking-wide text-zinc-500">Requests</div>
            <div className="mt-1 text-sm text-zinc-100">{requestCount}</div>
          </div>
        </div>
      )}
      <div className="mt-2 text-xs text-zinc-500">{activeConversation?.workspace?.path || "No workspace yet"}</div>
      <div className="mt-3 space-y-2">
        {activeConversation?.workspace?.files.length ? activeConversation.workspace.files.map((file) => (
          <div key={file.path} className="rounded-xl border border-zinc-800 bg-zinc-950/70 px-3 py-2 text-xs text-zinc-300">
            <div>{file.path}</div>
            <div className="mt-1 text-zinc-500">{file.size} bytes</div>
          </div>
        )) : (
          <div className="text-xs text-zinc-500">No workspace files yet.</div>
        )}
      </div>
      <div className="mt-4 border-t border-zinc-800 pt-4">
        <div className="flex items-center gap-2 text-sm font-medium text-zinc-100">
          <FileStack size={15} className="text-zinc-500" />
          Artifacts
        </div>
        <div className="mt-3 flex flex-wrap gap-2">
          {artifacts.slice(-6).reverse().map((artifact) => (
            <span key={artifact.id} className="rounded-full border border-zinc-700 bg-zinc-950 px-3 py-1 text-xs text-zinc-300">
              {artifact.artifact_type}
            </span>
          ))}
        </div>
      </div>
    </section>
  );
}