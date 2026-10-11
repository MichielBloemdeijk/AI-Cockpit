"use client";

import Link from "@/components/CockpitLink";
import { loginReturnUrl } from "@/lib/navigation";
import { useCallback, useEffect, useRef, useState } from "react";

import { acknowledgePushDelivery, checkAuth, getResult, type MaintainedResult } from "@/lib/api";
import { resultRenderers } from "./result-renderers";
import ResultSavedControl from "@/components/ResultSavedControl";
import { watchResult } from "@/lib/result-live";
import { resultViewTelemetry } from "@/lib/result-telemetry";

export default function ResultScreen({ publicId }: { publicId: string }) {
  const [result, setResult] = useState<MaintainedResult | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [connection, setConnection] = useState("live");
  const [slow, setSlow] = useState(false);
  const [copied, setCopied] = useState(false);
  const stateRevision = useRef(-1);
  const fetching = useRef(false);
  const refreshNeeded = useRef(false);
  const activePublicId = useRef(publicId);
  const observe = useRef<ReturnType<typeof resultViewTelemetry> | null>(null);
  activePublicId.current = publicId;

  const accept = useCallback((snapshot: MaintainedResult) => {
    if (snapshot.public_id !== activePublicId.current) return;
    if ((snapshot.state_revision ?? 0) < stateRevision.current) return;
    stateRevision.current = snapshot.state_revision ?? 0;
    setResult(snapshot); setError(null); setLoading(false);
  }, []);

  const refresh = useCallback(async () => {
    if (fetching.current) { refreshNeeded.current = true; return; }
    fetching.current = true;
    try {
      do {
        refreshNeeded.current = false;
        const snapshot = await getResult(publicId);
        accept(snapshot);
      } while (refreshNeeded.current && activePublicId.current === publicId);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "Unable to load result");
    } finally {
      fetching.current = false;
      setLoading(false);
    }
  }, [publicId, accept]);

  useEffect(() => {
    let disposed = false;
    let stop: (() => void) | undefined;
    stateRevision.current = -1;
    observe.current = resultViewTelemetry(publicId);
    void checkAuth().then((ok) => {
      if (disposed) return;
      if (!ok) {
        window.location.replace(loginReturnUrl());
      } else {
        void refresh();
        stop = watchResult(publicId, { snapshot: accept, reconcile: () => void refresh(), connection: setConnection,
          observe: kind => observe.current?.(kind) });
        const deliveryId = new URLSearchParams(window.location.search).get("delivery");
        if (deliveryId) void acknowledgePushDelivery(deliveryId, "opened").catch(() => {});
      }
    }).catch(() => { if (!disposed) { setError("Unable to check your session."); setLoading(false); } });
    return () => { disposed = true; stop?.(); observe.current = null; };
  }, [refresh, publicId, accept]);

  useEffect(() => {
    if (!result || result.public_id !== publicId) return;
    observe.current?.("first_snapshot", result.revision_number ?? 0);
    if (result.content && Object.hasOwn(resultRenderers, result.result_kind)) {
      observe.current?.("first_ready", result.revision_number ?? 0);
    }
  }, [result, publicId]);

  useEffect(() => {
    setSlow(false);
    if (!result || !["building", "updating"].includes(result.status)) return;
    const timer = window.setTimeout(() => setSlow(true), 30000);
    return () => window.clearTimeout(timer);
  }, [result?.status, result?.active_execution_run_id]);

  const renderer = result && Object.hasOwn(resultRenderers, result.result_kind) ? resultRenderers[result.result_kind] : null;
  const View = renderer?.View;
  const copy = async () => {
    if (!result?.content) return;
    const content = result.content;
    const text = "body" in content ? content.body : `${content.title}\n\n${content.ingredients.join("\n")}\n\n${content.instructions.join("\n")}\n\n${content.sources.map(source => source.url).join("\n")}`;
    try { await navigator.clipboard.writeText(text); setCopied(true); }
    catch { setError("Copy is unavailable in this browser. You can select the text instead."); }
  };
  return <main className="min-h-screen bg-zinc-950 px-4 py-8 text-zinc-100 sm:px-8">
    <div className="mx-auto max-w-3xl">
      <div className="mb-8 flex items-center justify-between gap-4 text-sm">
        <Link href={result?.app?.route_path || "/workspace/apps"} className="text-zinc-400 hover:text-zinc-100">← {result?.app?.title || "Apps"}</Link>
        {result && <Link href={`/tasks/${result.task_id}`} className="text-sky-400 hover:text-sky-300">View source task</Link>}
      </div>
      {loading && <p role="status" className="text-zinc-400">Loading result…</p>}
      {error && <div role="alert" className="rounded-xl border border-red-800 bg-red-950/30 p-4 text-red-300">{error}</div>}
      {connection !== "live" && <p role="status" className="mb-4 text-amber-300">{connection === "polling" ? "Live updates unavailable; checking periodically." : "Reconnecting live updates…"}</p>}
      {result && <article className="rounded-2xl border border-zinc-800 bg-zinc-900/60 p-5 shadow-xl sm:p-8">
        <p className="mb-2 text-xs uppercase tracking-widest text-sky-400">{renderer?.label}</p>
        <h1 className="text-2xl font-semibold tracking-tight sm:text-3xl">{result.title}</h1>
        {result.summary && <p className="mt-3 text-zinc-400">{result.summary}</p>}
        <div className="mt-4 flex flex-wrap items-center gap-3 print:hidden">
          <ResultSavedControl publicId={publicId} action={result.actions?.find(action => action.id === "set_saved")} refresh={() => void refresh()} />
          {result.content && <><button onClick={() => void copy()} className="rounded-lg border border-zinc-600 px-3 py-2 text-sm">{copied ? "Copied" : "Copy"}</button>
            <button onClick={() => window.print()} className="rounded-lg border border-zinc-600 px-3 py-2 text-sm">Print</button></>}
        </div>
        {result.progress_label && <p role="status" className="mt-4 text-zinc-400">{result.progress_label}</p>}
        {slow && <p role="status" className="mt-4 text-amber-300">This task is taking longer. <Link href={`/tasks/${result.task_id}`} className="underline">View the task</Link> for details.</p>}
        {result.warning && <p role="status" className="mt-4 rounded-lg border border-amber-800 p-3 text-amber-300">{result.warning}</p>}
        {!result.content && <p role="status" className="mt-8 text-zinc-400">{
          result.status === "failed" ? "This result could not be completed." :
          result.status === "cancelled" ? "This result was cancelled." :
          result.task_status_reason === "context_upload_pending" ? "Waiting for the captured screen…" :
          "Building this result…"
        }</p>}
        {result.content && <div className="mt-8 border-t border-zinc-800 pt-6">
          {View && <View content={result.content} publicId={publicId} />}
          {!View && <p role="alert">Unsupported result type. <Link href={`/tasks/${result.task_id}`} className="underline">View the source task</Link>.</p>}
        </div>}
      </article>}
    </div>
  </main>;
}
