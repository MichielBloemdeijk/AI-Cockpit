"use client";

import Link from "next/link";
import { useEffect, useState } from "react";
import { ArrowLeft, Blocks, ExternalLink, FileCode2, FolderTree, Route, ShieldCheck } from "lucide-react";
import { GeneratedAppDetail, getGeneratedAppBySlug } from "@/lib/api";


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

export default function GeneratedAppPage({ params }: { params: Promise<{ slug: string }> }) {
  const [app, setApp] = useState<GeneratedAppDetail | null>(null);
  const [slug, setSlug] = useState("");
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let active = true;
    const load = async () => {
      try {
        const resolved = await params;
        if (!active) return;
        setSlug(resolved.slug);
        const detail = await getGeneratedAppBySlug(resolved.slug);
        if (!active) return;
        setApp(detail);
        setError(null);
      } catch (nextError) {
        if (!active) return;
        setError(nextError instanceof Error ? nextError.message : "Failed to load app.");
      } finally {
        if (active) setLoading(false);
      }
    };

    void load();
    return () => {
      active = false;
    };
  }, [params]);

  if (loading) {
    return <div className="p-6 text-sm text-zinc-500">Loading app surface...</div>;
  }

  if (error || !app) {
    return (
      <div className="p-6">
        <Link href="/workspace/apps" className="mb-4 inline-flex items-center gap-2 text-sm text-zinc-400 transition-colors hover:text-zinc-200">
          <ArrowLeft size={14} />
          Back to apps
        </Link>
        <div className="rounded-3xl border border-rose-500/30 bg-rose-500/10 p-5 text-sm text-rose-200">
          {error || `App '${slug}' was not found.`}
        </div>
      </div>
    );
  }

  return (
    <div className="flex h-full flex-col overflow-y-auto">
      <div className="border-b border-zinc-800 bg-zinc-900/90 px-4 py-4">
        <div className="flex flex-wrap items-center gap-3">
          <Link href="/workspace/apps" className="inline-flex items-center gap-2 text-sm text-zinc-400 transition-colors hover:text-zinc-200">
            <ArrowLeft size={14} />
            Back to apps
          </Link>
          <Link href={`/apps/${app.slug}`} className="inline-flex items-center gap-2 text-sm text-blue-300 transition-colors hover:text-blue-200">
            Open live surface
            <ExternalLink size={14} />
          </Link>
        </div>
        <div className="mt-4 flex flex-col gap-4 lg:flex-row lg:items-start lg:justify-between">
          <div className="min-w-0">
            <div className="flex items-center gap-3">
              <div className="flex h-12 w-12 items-center justify-center rounded-2xl border border-zinc-800 bg-zinc-950 text-zinc-300">
                <Blocks size={20} />
              </div>
              <div>
                <h1 className="text-2xl font-semibold text-zinc-100">{app.title}</h1>
                <div className="mt-1 text-sm text-zinc-500">{app.route_path}</div>
              </div>
            </div>
            <p className="mt-4 max-w-3xl text-sm text-zinc-400">
              {app.description || "This generated app has a reserved cockpit route and explicit repo write boundary. The chat agent can attach directly to this surface and expand it with nested routes under the app directory."}
            </p>
          </div>
          <div className={`self-start rounded-full border px-3 py-1.5 text-xs font-medium ${statusClasses(app.status)}`}>
            {app.status.replaceAll("_", " ")}
          </div>
        </div>
      </div>

      <div className="grid gap-4 p-4 lg:grid-cols-[1.15fr_0.85fr]">
        <section className="rounded-3xl border border-zinc-800 bg-zinc-900/70 p-4">
          <div className="flex items-center gap-2 text-zinc-100">
            <Route size={16} className="text-zinc-500" />
            <h2 className="text-sm font-semibold">Generated App Surface</h2>
          </div>
          <div className="mt-4 rounded-3xl border border-dashed border-zinc-700 bg-[radial-gradient(circle_at_top,_rgba(59,130,246,0.14),_transparent_45%),linear-gradient(180deg,rgba(24,24,27,0.95),rgba(9,9,11,0.98))] p-6">
            <div className="text-xs uppercase tracking-[0.25em] text-zinc-500">Current host state</div>
            <div className="mt-3 text-2xl font-semibold text-zinc-100">{app.title}</div>
            <p className="mt-3 max-w-2xl text-sm text-zinc-400">
              This route is now live for preview. Add or update page.tsx files under the generated app directory to shape what users and the chat agent see at runtime.
            </p>
            <div className="mt-5 grid gap-3 md:grid-cols-2">
              <div className="rounded-2xl border border-zinc-800 bg-zinc-950/70 p-4">
                <div className="text-xs uppercase tracking-[0.2em] text-zinc-500">Verification</div>
                <div className="mt-2 text-sm text-zinc-100">{app.verification_status || "Not started"}</div>
              </div>
              <div className="rounded-2xl border border-zinc-800 bg-zinc-950/70 p-4">
                <div className="text-xs uppercase tracking-[0.2em] text-zinc-500">Provenance</div>
                <div className="mt-2 space-y-1 text-sm text-zinc-300">
                  <div>Source run: {app.source_task_run_id || "Not linked yet"}</div>
                  <div>Conversation: {app.source_conversation_id || "Not linked yet"}</div>
                </div>
              </div>
            </div>
          </div>
        </section>

        <section className="space-y-4">
          <div className="rounded-3xl border border-zinc-800 bg-zinc-900/70 p-4">
            <div className="flex items-center gap-2 text-zinc-100">
              <FileCode2 size={16} className="text-zinc-500" />
              <h2 className="text-sm font-semibold">Frontend Contract</h2>
            </div>
            <div className="mt-4 space-y-3 text-sm text-zinc-300">
              <div className="rounded-2xl border border-zinc-800 bg-zinc-950/70 p-3">
                <div className="text-xs uppercase tracking-[0.2em] text-zinc-500">Generated root</div>
                <div className="mt-2 break-all">{app.frontend_root}</div>
              </div>
              <div className="rounded-2xl border border-zinc-800 bg-zinc-950/70 p-3">
                <div className="text-xs uppercase tracking-[0.2em] text-zinc-500">Expected entry file</div>
                <div className="mt-2 break-all">{app.frontend_entry_path || "Not assigned"}</div>
              </div>
            </div>
          </div>

          <div className="rounded-3xl border border-zinc-800 bg-zinc-900/70 p-4">
            <div className="flex items-center gap-2 text-zinc-100">
              <ShieldCheck size={16} className="text-zinc-500" />
              <h2 className="text-sm font-semibold">Explicit Write Boundary</h2>
            </div>
            <div className="mt-4 space-y-3">
              {app.allowed_write_roots.map((root) => (
                <div key={root} className="rounded-2xl border border-zinc-800 bg-zinc-950/70 p-3 text-sm text-zinc-300">
                  {root}
                </div>
              ))}
            </div>
            <p className="mt-4 text-xs text-zinc-500">
              Agent runs can be granted these roots explicitly. Writes to other repo paths are blocked instead of being widened silently.
            </p>
          </div>

          <div className="rounded-3xl border border-zinc-800 bg-zinc-900/70 p-4">
            <div className="flex items-center gap-2 text-zinc-100">
              <FolderTree size={16} className="text-zinc-500" />
              <h2 className="text-sm font-semibold">Manifest Snapshot</h2>
            </div>
            <pre className="mt-4 overflow-x-auto rounded-2xl border border-zinc-800 bg-zinc-950/70 p-3 text-xs leading-6 text-zinc-400">
              {JSON.stringify(app.manifest_json || {}, null, 2)}
            </pre>
            {app.last_error ? (
              <div className="mt-3 rounded-2xl border border-rose-500/30 bg-rose-500/10 p-3 text-sm text-rose-200">
                {app.last_error}
              </div>
            ) : null}
          </div>
        </section>
      </div>
    </div>
  );
}