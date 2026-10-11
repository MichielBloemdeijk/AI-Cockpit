"use client";

import { useEffect, useState } from "react";
import TaskDetailPage from "../../../app/(app)/tasks/[taskId]/page";
import AuthenticatedShell from "../AuthenticatedShell";

export default function Page() {
  const [params, setParams] = useState<Promise<{ taskId: string }> | null>(null);
  useEffect(() => {
    const match = /^\/tasks\/([^/]+)\/?$/.exec(window.location.pathname);
    if (match) {
      try { setParams(Promise.resolve({ taskId: decodeURIComponent(match[1]) })); }
      catch { /* Malformed path: no private data request. */ }
    }
  }, []);
  return <AuthenticatedShell>{params ? <TaskDetailPage params={params} /> : <p className="p-6">Loading task…</p>}</AuthenticatedShell>;
}
