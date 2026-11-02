"use client";

import { useCallback, useEffect, useState } from "react";
import Link from "@/components/CockpitLink";
import { Blocks, RefreshCw } from "lucide-react";
import { listPlatformApps, type PlatformApp } from "@/lib/app-platform";

/**
 * The installed apps. The static companion has no app host, so it passes
 * `appsAvailable={false}` and the screen points to the full Cockpit instead.
 */
export function AppsScreen({ appsAvailable }: { appsAvailable: boolean }) {
  const [installed, setInstalled] = useState<PlatformApp[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const refresh = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      setInstalled(appsAvailable ? await listPlatformApps() : []);
    } catch (nextError) {
      setError(nextError instanceof Error ? nextError.message : "Failed to load apps.");
    } finally {
      setLoading(false);
    }
  }, [appsAvailable]);

  useEffect(() => {
    void refresh();

    const handlePageShow = (event: PageTransitionEvent) => {
      if (event.persisted) {
        void refresh();
      }
    };

    window.addEventListener("pageshow", handlePageShow);
    return () => {
      window.removeEventListener("pageshow", handlePageShow);
    };
  }, [refresh]);

  return (
    <div className="flex h-full flex-col">
      <div className="flex shrink-0 items-center justify-between border-b border-zinc-700 bg-zinc-900 px-4 py-3">
        <div className="flex items-center gap-2">
          <Blocks size={18} className="text-zinc-400" />
          <span className="text-sm font-medium text-zinc-100">Apps</span>
          <span className="text-xs text-zinc-500">({installed.length})</span>
        </div>
        <button
          onClick={() => void refresh()}
          className="rounded-lg p-1.5 text-zinc-500 transition-colors hover:bg-zinc-800 hover:text-zinc-300"
          title="Refresh"
        >
          <RefreshCw size={15} />
        </button>
      </div>

      <div className="flex-1 overflow-y-auto p-4">
        {error ? <div className="mb-4 rounded-2xl border border-rose-500/30 bg-rose-500/10 px-4 py-3 text-sm text-rose-200">{error}</div> : null}
        {!appsAvailable ? (
          <p className="text-sm text-zinc-400">Apps are available in the full Cockpit frontend.</p>
        ) : loading && installed.length === 0 ? (
          <div className="py-12 text-center text-sm text-zinc-500">Loading apps...</div>
        ) : installed.length === 0 ? (
          <div className="flex h-full flex-col items-center justify-center px-8 text-center">
            <div className="mb-4 flex h-16 w-16 items-center justify-center rounded-2xl bg-zinc-800">
              <Blocks size={28} className="text-zinc-500" />
            </div>
            <h2 className="mb-2 text-lg font-semibold text-zinc-300">No apps yet</h2>
            <p className="max-w-md text-sm text-zinc-500">Ask Jarvis to build one.</p>
          </div>
        ) : (
          <div className="grid gap-4 md:grid-cols-2">
            {installed.map((app) => (
              <article key={app.slug} className="rounded-2xl border border-zinc-700 bg-zinc-900 p-4">
                <div className="flex items-baseline justify-between gap-3">
                  <h3 className="truncate text-lg font-semibold text-zinc-100">{app.title}</h3>
                  <span className="text-xs text-zinc-500">v{app.version}</span>
                </div>
                <p className="my-3 text-sm text-zinc-400">{app.description}</p>
                <Link href={app.url} className="text-sky-400">Open app</Link>
              </article>
            ))}
          </div>
        )}
      </div>
    </div>
  );
}
