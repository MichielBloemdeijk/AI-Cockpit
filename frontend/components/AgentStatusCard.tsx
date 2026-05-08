"use client";

import { AlertTriangle, Loader2, PauseCircle } from "lucide-react";
import clsx from "clsx";
import type { AgentStatus } from "@/lib/hooks";

interface Props {
  status: AgentStatus;
}

export function AgentStatusCard({ status }: Props) {
  const icon = status.tone === "error"
    ? <AlertTriangle size={16} />
    : status.active
      ? <Loader2 size={16} className="animate-spin" />
      : <PauseCircle size={16} />;

  const toneClasses = status.tone === "error"
    ? "border-rose-500/30 bg-rose-500/10 text-rose-100"
    : status.tone === "warning"
      ? "border-amber-500/30 bg-amber-500/10 text-amber-100"
      : "border-sky-500/30 bg-sky-500/10 text-sky-100";

  const secondaryTextClasses = status.tone === "error"
    ? "text-rose-100/80"
    : status.tone === "warning"
      ? "text-amber-100/80"
      : "text-sky-100/80";

  return (
    <div className="mb-4 px-3 md:px-4">
      <div className={clsx("rounded-2xl border px-4 py-3", toneClasses)}>
        <div className="flex items-start gap-3">
          <div className="mt-0.5 shrink-0">{icon}</div>
          <div className="min-w-0 flex-1">
            <div className="text-[11px] uppercase tracking-[0.16em]">{status.title}</div>
            <div className={clsx("mt-1 text-sm leading-6", secondaryTextClasses)}>{status.content}</div>
          </div>
        </div>
      </div>
    </div>
  );
}