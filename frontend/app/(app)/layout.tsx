"use client";

import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import { Sidebar } from "@/components/Sidebar";
import { checkAuth } from "@/lib/api";

export default function AppLayout({ children }: { children: React.ReactNode }) {
  const router = useRouter();
  const [authenticated, setAuthenticated] = useState(false);

  useEffect(() => {
    let active = true;
    checkAuth().then((ok) => {
      if (!active) return;
      if (!ok) {
        const next = `${window.location.pathname}${window.location.search}${window.location.hash}`;
        router.replace(`/login?next=${encodeURIComponent(next)}`);
      } else setAuthenticated(true);
    });
    return () => { active = false; };
  }, [router]);

  if (!authenticated) return <main className="p-6 text-zinc-300">Connecting to Jarvis…</main>;
  return (
    <div className="fixed inset-0 flex overflow-hidden bg-zinc-950 md:flex-row">
      <Sidebar />
      <main className="mobile-safe-main flex min-h-0 min-w-0 flex-1 flex-col overflow-hidden">
        {children}
      </main>
    </div>
  );
}
