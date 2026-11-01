"use client";

import { useEffect } from "react";
import { useRouter } from "next/navigation";
import { checkAuth } from "@/lib/api";

export default function AppsLayout({ children }: { children: React.ReactNode }) {
  const router = useRouter();

  useEffect(() => {
    checkAuth().then((ok) => {
      if (!ok) router.push("/login");
    });
  }, [router]);

  return <>{children}</>;
}