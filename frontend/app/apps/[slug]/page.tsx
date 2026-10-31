"use client";

import { Suspense, useEffect, useRef, useState } from "react";
import Link from "next/link";
import { useSearchParams } from "next/navigation";
import { AlertTriangle, ArrowLeft } from "lucide-react";
import { acknowledgePushDelivery } from "@/lib/api";
import { appTarget, createAppBridge, openAppSession, type AppBridge, type AppSession } from "@/lib/app-platform";

/** Hosts an installed app in a sandboxed frame and serves its SDK requests. */
export default function AppHostPage({ params }: { params: Promise<{ slug: string }> }) {
  return <Suspense fallback={<div className="p-6 text-sm text-zinc-500">Opening app…</div>}>
    <AppHost params={params} />
  </Suspense>;
}

function AppHost({ params }: { params: Promise<{ slug: string }> }) {
  const [session, setSession] = useState<AppSession | null>(null);
  const [error, setError] = useState<string | null>(null);
  const frameRef = useRef<HTMLIFrameElement>(null);
  const bridgeRef = useRef<AppBridge | null>(null);
  // A link may open the app on one record (`?collection=recipe&key=…`).
  const search = useSearchParams();
  const collection = search.get("collection");
  const key = search.get("key");
  const delivery = search.get("delivery");

  useEffect(() => {
    // Opened from a result notification.
    if (delivery) void acknowledgePushDelivery(delivery, "opened").catch(() => undefined);
  }, [delivery]);

  useEffect(() => {
    let active = true;
    params
      .then(({ slug }) => openAppSession(slug))
      .then((next) => { if (active) setSession(next); })
      .catch((cause: unknown) => { if (active) setError(cause instanceof Error ? cause.message : "Failed to open app."); });
    return () => { active = false; };
  }, [params]);

  useEffect(() => {
    if (!session) return;
    const bridge = createAppBridge({
      session,
      frame: () => frameRef.current?.contentWindow ?? null,
      renewSession: () => openAppSession(session.slug),
      target: appTarget(new URLSearchParams(window.location.search)),
    });
    bridgeRef.current = bridge;
    window.addEventListener("message", bridge.onMessage);
    return () => {
      window.removeEventListener("message", bridge.onMessage);
      bridgeRef.current = null;
    };
  }, [session]);

  useEffect(() => {
    // Opening another record while the app runs (for example from a notification).
    bridgeRef.current?.showTarget(collection && key ? { collection, key } : null);
  }, [collection, key]);

  return (
    <div className="flex h-full min-h-0 flex-col bg-zinc-950">
      <div className="flex shrink-0 items-center gap-3 border-b border-zinc-800 px-4 py-2">
        <Link href="/workspace/apps" className="text-zinc-400 hover:text-zinc-200" title="All apps">
          <ArrowLeft size={16} />
        </Link>
        <span className="truncate text-sm font-medium text-zinc-100">{session?.title ?? "App"}</span>
        {session && <span className="text-xs text-zinc-500">v{session.version}</span>}
      </div>
      {error ? (
        <div className="m-4 flex items-start gap-2 rounded-2xl border border-rose-500/30 bg-rose-500/10 px-4 py-3 text-sm text-rose-200">
          <AlertTriangle size={16} className="mt-0.5 shrink-0" />
          <span>{error}</span>
        </div>
      ) : session ? (
        <iframe
          ref={frameRef}
          key={`${session.slug}:${session.version}`}
          title={session.title}
          src={session.frame_url}
          sandbox="allow-scripts allow-forms allow-popups allow-modals"
          className="min-h-0 w-full flex-1 border-0 bg-zinc-950"
        />
      ) : (
        <div className="p-6 text-sm text-zinc-500">Opening app…</div>
      )}
    </div>
  );
}
