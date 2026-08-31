"use client";

import { useEffect } from "react";
import { useRouter } from "next/navigation";
import { Sidebar } from "@/components/Sidebar";
import { checkAuth } from "@/lib/api";

export default function AppLayout({ children }: { children: React.ReactNode }) {
  const router = useRouter();

  useEffect(() => {
    checkAuth().then((ok) => {
      if (!ok) {
        const next = `${window.location.pathname}${window.location.search}${window.location.hash}`;
        router.replace(`/login?next=${encodeURIComponent(next)}`);
      }
    });
  }, [router]);

  return (
    <div className="flex h-[100dvh] overflow-hidden bg-zinc-950 md:flex-row">
      <Sidebar />
      <main className="flex min-w-0 flex-1 flex-col overflow-hidden pb-[calc(5.25rem+env(safe-area-inset-bottom))] md:pb-0">
        {children}
      </main>
    </div>
  );
}
