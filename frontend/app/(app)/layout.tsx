"use client";

import { useEffect } from "react";
import { useRouter } from "next/navigation";
import { Sidebar } from "@/components/Sidebar";
import { checkAuth } from "@/lib/api";

export default function AppLayout({ children }: { children: React.ReactNode }) {
  const router = useRouter();

  useEffect(() => {
    checkAuth().then((ok) => {
      if (!ok) router.push("/login");
    });
  }, [router]);

  return (
    <div className="flex h-full bg-zinc-950 md:flex-row">
      <Sidebar />
      <main className="flex min-w-0 flex-1 flex-col overflow-hidden pb-24 md:pb-0">
        {children}
      </main>
    </div>
  );
}
