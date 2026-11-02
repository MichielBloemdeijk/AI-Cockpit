"use client";

import { useState } from "react";
import { FlaskConical } from "lucide-react";

import { injectFakeJarvisEvent, type JarvisFakeProviderEventRequest, type JarvisSession } from "@/lib/api/jarvis";
import { resolveJarvisWakeGateMode, type JarvisWakeGateMode } from "@/lib/jarvis/benchmark-preferences";
import { createUuid } from "@/lib/uuid";

/**
 * Developer-mode controls for the deterministic fake provider and the wake-gate
 * benchmark. Shown only in developer mode; nothing here runs in normal use.
 */
export function DeveloperLab({
  session,
  hasLog,
  busy,
  wakeGateMode,
  wakeGateLoaded,
  onWakeGateMode,
  onSupersede,
  run,
}: {
  session: JarvisSession | null;
  hasLog: boolean;
  busy: boolean;
  wakeGateMode: JarvisWakeGateMode;
  wakeGateLoaded: boolean;
  onWakeGateMode: (mode: JarvisWakeGateMode) => void;
  onSupersede: () => Promise<void>;
  run: (action: () => Promise<unknown>) => void;
}) {
  const [open, setOpen] = useState(false);
  const ready = session?.lifecycle_state === "ready";
  const effective = resolveJarvisWakeGateMode(true, wakeGateMode);
  const inject = (kind: JarvisFakeProviderEventRequest["kind"], payload: Record<string, unknown> = {}) => run(async () => {
    if (!session) throw new Error("Start a fake controller first");
    await injectFakeJarvisEvent(session.id, { generation: session.generation, event_id: `lab-${createUuid()}`, kind, payload });
  });
  return (
    <div className="shrink-0 border-b border-violet-500/20 bg-zinc-950 px-3 py-2 md:px-6">
      <div className="mx-auto max-w-3xl rounded-2xl border border-dashed border-violet-500/30 bg-violet-500/5">
        <button type="button" onClick={() => setOpen((value) => !value)} className="flex min-h-12 w-full items-center justify-between px-3 py-2 text-left text-xs font-medium text-violet-200">
          <span className="flex items-center gap-2"><FlaskConical size={14} /> Developer conversation lab</span>
          <span>{open ? "Hide" : "Show"}</span>
        </button>
        {open && (
          <div className="grid grid-cols-2 gap-2 border-t border-violet-500/20 p-3">
            <label className="col-span-2 rounded-xl border border-violet-500/20 bg-zinc-950/40 p-2 text-xs text-violet-100">
              <span className="block font-medium">Wake gate benchmark</span>
              <select
                value={wakeGateMode}
                disabled={!wakeGateLoaded}
                onChange={(event) => onWakeGateMode(event.target.value as JarvisWakeGateMode)}
                className="mt-2 min-h-10 w-full rounded-lg border border-zinc-700 bg-zinc-900 px-2 text-zinc-200"
              >
                <option value="transcript_rules">Transcript rules (default)</option>
                <option value="immediate">Immediate (developer)</option>
              </select>
              <span className="mt-1 block text-[11px] text-zinc-500">
                Selected: {wakeGateMode}; effective: {effective}. Immediate mode is available only while developer mode is enabled.
              </span>
            </label>
            <LabButton label="User speaking" disabled={!ready || busy} onClick={() => inject("user_speaking")} />
            <LabButton label="Release floor" disabled={!ready || busy} onClick={() => inject("user_stopped")} />
            <LabButton label="Direct reply" disabled={!ready || busy} onClick={() => inject("assistant_response", { response_id: `lab-${createUuid()}`, item_id: `lab-${createUuid()}`, transcript: "This is a deterministic direct response." })} />
            <LabButton label="Interrupt reply" disabled={!ready || busy} onClick={() => inject("assistant_interrupted", { response_id: `lab-${createUuid()}`, item_id: `lab-${createUuid()}`, transcript: "This response was interrupted before it finished.", heard_transcript: "This response was interrupted" })} />
            <LabButton label="Delegate demo" disabled={!ready || busy} onClick={() => inject("shell_call", { call_id: `lab-${createUuid()}`, name: "dispatch_task", arguments: { goal: "Prepare the developer lab report" } })} />
            <LabButton label="Supersede" disabled={!hasLog || busy} onClick={() => run(onSupersede)} />
            <LabButton label="Provider error" disabled={!ready || busy} onClick={() => inject("provider_error", { reason: "simulated_failure" })} />
          </div>
        )}
      </div>
    </div>
  );
}

function LabButton({ label, disabled, onClick }: { label: string; disabled: boolean; onClick: () => void }) {
  return (
    <button type="button" disabled={disabled} onClick={onClick} className="rounded-lg border border-zinc-700 bg-zinc-900 px-2 py-2 text-[11px] text-zinc-300 hover:border-violet-500/40 disabled:opacity-40">
      {label}
    </button>
  );
}
