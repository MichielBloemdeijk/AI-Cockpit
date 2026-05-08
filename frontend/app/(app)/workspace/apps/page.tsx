"use client";

import Link from "next/link";
import { useEffect, useState } from "react";
import { Blocks, ExternalLink, Plus, RefreshCw } from "lucide-react";
import {
  createGeneratedApp,
  GeneratedAppSummary,
  listGeneratedApps,
} from "@/lib/api";


function statusClasses(status: string): string {
  switch (status) {
    case "verified":
      return "border-emerald-500/30 bg-emerald-500/10 text-emerald-300";
    case "ready_for_test":
      return "border-amber-500/30 bg-amber-500/10 text-amber-300";
    case "failed":
      return "border-rose-500/30 bg-rose-500/10 text-rose-300";
    case "building":
      return "border-sky-500/30 bg-sky-500/10 text-sky-300";
    default:
      return "border-zinc-700 bg-zinc-800 text-zinc-300";
  }
}

export default function AppsPage() {
  const [apps, setApps] = useState<GeneratedAppSummary[]>([]);
  const [loading, setLoading] = useState(true);
  const [creating, setCreating] = useState(false);
  const [showNew, setShowNew] = useState(false);
  const [title, setTitle] = useState("");
  const [description, setDescription] = useState("");
  const [error, setError] = useState<string | null>(null);

  const refresh = async () => {
    setLoading(true);
    setError(null);
    try {
      setApps(await listGeneratedApps());
    } catch (nextError) {
      setError(nextError instanceof Error ? nextError.message : "Failed to load apps.");
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    void refresh();
  }, []);

  const handleCreate = async () => {
    if (!title.trim()) return;
    setCreating(true);
    setError(null);
    try {
      await createGeneratedApp({
        title: title.trim(),
        description: description.trim() || undefined,
        status: "draft",
      });
      setTitle("");
      setDescription("");
      setShowNew(false);
      await refresh();
    } catch (nextError) {
      setError(nextError instanceof Error ? nextError.message : "Failed to create app.");
    } finally {
      setCreating(false);
    }
  };

  return (
    <div className="flex h-full flex-col">
      <div className="flex shrink-0 items-center justify-between border-b border-zinc-700 bg-zinc-900 px-4 py-3">
        <div className="flex items-center gap-2">
          <Blocks size={18} className="text-zinc-400" />
          <span className="text-sm font-medium text-zinc-100">Apps</span>
          <span className="text-xs text-zinc-500">({apps.length})</span>
        </div>
        <div className="flex items-center gap-2">
          <button
            onClick={() => void refresh()}
            className="rounded-lg p-1.5 text-zinc-500 transition-colors hover:bg-zinc-800 hover:text-zinc-300"
            title="Refresh"
          >
            <RefreshCw size={15} />
          </button>
          <button
            onClick={() => setShowNew((value) => !value)}
            className="flex items-center gap-1.5 rounded-lg bg-blue-600 px-3 py-1.5 text-xs text-white transition-colors hover:bg-blue-700"
          >
            <Plus size={13} />
            New draft app
          </button>
        </div>
      </div>

      {showNew && (
        <div className="shrink-0 border-b border-zinc-700 bg-zinc-900/60 px-4 py-4">
          <div className="grid gap-3 md:grid-cols-[1.2fr_1fr_auto] md:items-end">
            <label className="space-y-2">
              <span className="text-xs uppercase tracking-[0.2em] text-zinc-500">Title</span>
              <input
                value={title}
                onChange={(event) => setTitle(event.target.value)}
                placeholder="Example: Flappy Bird"
                className="w-full rounded-xl border border-zinc-700 bg-zinc-800 px-3 py-2 text-sm text-zinc-100 placeholder-zinc-500 focus:border-blue-500 focus:outline-none"
              />
            </label>
            <label className="space-y-2">
              <span className="text-xs uppercase tracking-[0.2em] text-zinc-500">Description</span>
              <input
                value={description}
                onChange={(event) => setDescription(event.target.value)}
                placeholder="Short note about the draft"
                className="w-full rounded-xl border border-zinc-700 bg-zinc-800 px-3 py-2 text-sm text-zinc-100 placeholder-zinc-500 focus:border-blue-500 focus:outline-none"
              />
            </label>
            <button
              onClick={handleCreate}
              disabled={creating || !title.trim()}
              className="rounded-xl bg-blue-600 px-4 py-2 text-sm font-medium text-white transition-colors hover:bg-blue-700 disabled:opacity-50"
            >
              {creating ? "Creating..." : "Create draft"}
            </button>
          </div>
          <p className="mt-3 text-xs text-zinc-500">
            Draft apps reserve a stable /apps route and explicit generated-app write roots before any agent implementation work begins.
          </p>
        </div>
      )}

      <div className="flex-1 overflow-y-auto p-4">
        {error ? <div className="mb-4 rounded-2xl border border-rose-500/30 bg-rose-500/10 px-4 py-3 text-sm text-rose-200">{error}</div> : null}
        {loading ? (
          <div className="py-12 text-center text-sm text-zinc-500">Loading apps...</div>
        ) : apps.length === 0 ? (
          <div className="flex h-full flex-col items-center justify-center px-8 text-center">
            <div className="mb-4 flex h-16 w-16 items-center justify-center rounded-2xl bg-zinc-800">
              <Blocks size={28} className="text-zinc-500" />
            </div>
            <h2 className="mb-2 text-lg font-semibold text-zinc-300">No generated apps yet</h2>
            <p className="max-w-2xl text-sm text-zinc-500">
              Create a draft app to reserve its route, frontend contract, and explicit write boundary. Build and edit it from the main chat flow once the app is attached.
            </p>
          </div>
        ) : (
          <div className="grid gap-4 lg:grid-cols-2 xl:grid-cols-3">
            {apps.map((app) => (
              <article key={app.id} className="rounded-3xl border border-zinc-800 bg-zinc-900/70 p-4">
                <div className="flex items-start justify-between gap-3">
                  <div className="min-w-0">
                    <div className="truncate text-base font-semibold text-zinc-100">{app.title}</div>
                    <div className="mt-1 text-xs text-zinc-500">/{app.slug}</div>
                  </div>
                  <span className={`rounded-full border px-2.5 py-1 text-[11px] font-medium ${statusClasses(app.status)}`}>
                    {app.status.replaceAll("_", " ")}
                  </span>
                </div>

                <p className="mt-3 min-h-12 text-sm text-zinc-400">
                  {app.description || "No description yet. This draft reserves the route and generated-app contract."}
                </p>

                <div className="mt-4 space-y-2 rounded-2xl border border-zinc-800 bg-zinc-950/60 p-3 text-xs text-zinc-400">
                  <div className="flex items-center justify-between gap-3">
                    <span>Route</span>
                    <span className="truncate text-zinc-300">{app.route_path}</span>
                  </div>
                  <div className="flex items-center justify-between gap-3">
                    <span>Verification</span>
                    <span className="truncate text-zinc-300">{app.verification_status || "not started"}</span>
                  </div>
                  <div className="flex items-center justify-between gap-3">
                    <span>Updated</span>
                    <span className="truncate text-zinc-300">{new Date(app.updated_at).toLocaleString()}</span>
                  </div>
                </div>

                <div className="mt-4 flex gap-2">
                  <Link
                    href={`/apps/${app.slug}`}
                    className="inline-flex flex-1 items-center justify-center gap-2 rounded-xl bg-blue-600 px-3 py-2 text-sm font-medium text-white transition-colors hover:bg-blue-700"
                  >
                    Open surface
                    <ExternalLink size={14} />
                  </Link>
                  <Link
                    href={`/workspace/apps/${app.slug}`}
                    className="inline-flex items-center justify-center rounded-xl border border-zinc-700 px-3 py-2 text-sm text-zinc-300 transition-colors hover:bg-zinc-800 hover:text-zinc-100"
                  >
                    Details
                  </Link>
                </div>
              </article>
            ))}
          </div>
        )}
      </div>
    </div>
  );
}