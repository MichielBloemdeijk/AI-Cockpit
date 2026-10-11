"use client";

import { useEffect, useState, type ReactNode } from "react";
import { checkAuth } from "@/lib/api";
import { loginReturnUrl } from "@/lib/navigation";
import Link from "@/components/CockpitLink";

// Result companion UI, not a second implementation of the full Cockpit.
export default function AuthenticatedShell({ children }: { children: ReactNode }) {
  const [ready, setReady] = useState(false);
  const [error, setError] = useState(false);
  useEffect(() => {
    let active = true;
    checkAuth().then(ok => {
      if (!active) return;
      if (ok) setReady(true);
      else window.location.replace(loginReturnUrl());
    }).catch(() => { if (active) setError(true); });
    return () => { active = false; };
  }, []);
  if (!ready) return <p className="p-6">{error ? "Unable to check your session. Reload to retry." : "Loading…"}</p>;
  return <div className="flex h-dvh flex-col"><nav className="flex gap-5 border-b border-zinc-800 p-3 text-sky-400"><Link href="/workspace/apps">Apps</Link><Link href="/tasks">Tasks</Link></nav><div className="min-h-0 flex-1">{children}</div></div>;
}
