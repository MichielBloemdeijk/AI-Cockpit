"use client";

import Link from "next/link";
import { useEffect, useState } from "react";
import { AlertTriangle, ArrowLeft, ExternalLink } from "lucide-react";
import { GeneratedAppDetail, getGeneratedAppBySlug } from "@/lib/api";

export default function GeneratedAppHostPage({ params }: { params: Promise<{ slug: string }> }) {
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
    return <div className="p-6 text-sm text-zinc-500">Loading isolated app surface...</div>;
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

  const failureText = app.last_error || (app.status === "failed" ? "This generated app is currently failing." : null);

  return (
    <div className="flex h-full flex-col overflow-y-auto bg-[radial-gradient(circle_at_top,_rgba(96,165,250,0.14),_transparent_40%),linear-gradient(180deg,#111827_0%,#09090b_100%)] text-zinc-100">
      <div className="border-b border-zinc-800 bg-zinc-950/70 px-4 py-4">
        <div className="flex flex-wrap items-center gap-3">
          <Link href="/workspace/apps" className="inline-flex items-center gap-2 text-sm text-zinc-400 transition-colors hover:text-zinc-200">
            <ArrowLeft size={14} />
            Back to apps
          </Link>
          <Link href={`/workspace/apps/${app.slug}`} className="inline-flex items-center gap-2 text-sm text-blue-300 transition-colors hover:text-blue-200">
            Open app details
            <ExternalLink size={14} />
          </Link>
        </div>
        <div className="mt-4">
          <div className="text-xs uppercase tracking-[0.24em] text-zinc-500">App status</div>
          <h1 className="mt-2 text-3xl font-semibold">{app.title}</h1>
        </div>
      </div>

      <div className="grid gap-4 p-4 lg:grid-cols-[1.2fr_0.8fr]">
        <section className="rounded-3xl border border-zinc-800 bg-zinc-900/70 p-5">
          <div className="flex items-start gap-3">
            <div className="mt-0.5 rounded-2xl border border-amber-500/30 bg-amber-500/10 p-2 text-amber-300">
              <AlertTriangle size={18} />
            </div>
            <div>
              <h2 className="text-lg font-semibold text-zinc-100">Error details</h2>
            </div>
          </div>

          <div className="mt-5 rounded-2xl border border-zinc-800 bg-zinc-950/60 p-4 text-sm text-zinc-300">
            <div className="text-xs uppercase tracking-[0.2em] text-zinc-500">Current state</div>
            <div className="mt-3 space-y-2">
              <div>Status: {app.status.replaceAll("_", " ")}</div>
              <div>Verification: {app.verification_status || "not started"}</div>
              <div>Route: {app.route_path}</div>
              <div>App root: {app.frontend_root}</div>
            </div>
          </div>

          <div className="mt-4 rounded-2xl border border-zinc-800 bg-zinc-950/60 p-4 text-sm text-zinc-300">
            <div className="text-xs uppercase tracking-[0.2em] text-zinc-500">Recorded error</div>
            <div className="mt-3 max-h-[32rem] overflow-auto rounded-2xl border border-zinc-800 bg-zinc-950 p-3">
              <pre className="min-w-max whitespace-pre-wrap break-words font-mono text-xs leading-6 text-zinc-300">
                {failureText || "No app-specific error has been recorded."}
              </pre>
            </div>
          </div>
        </section>

        <aside className="space-y-4">
          <div className="rounded-3xl border border-zinc-800 bg-zinc-900/70 p-4 text-sm text-zinc-300">
            <div className="text-xs uppercase tracking-[0.2em] text-zinc-500">Timestamps</div>
            <div className="mt-3 space-y-2 text-zinc-400">
              <div>Updated: {new Date(app.updated_at).toLocaleString()}</div>
              <div>Created: {new Date(app.created_at).toLocaleString()}</div>
            </div>
          </div>

          <div className="rounded-3xl border border-zinc-800 bg-zinc-900/70 p-4 text-sm text-zinc-300">
            <div className="text-xs uppercase tracking-[0.2em] text-zinc-500">Source locations</div>
            <div className="mt-3 space-y-2 break-all text-zinc-400">
              <div>{app.frontend_entry_path || "No entry file recorded"}</div>
              {app.allowed_write_roots.map((root) => (
                <div key={root}>{root}</div>
              ))}
            </div>
          </div>
        </aside>
      </div>
    </div>
  );
}