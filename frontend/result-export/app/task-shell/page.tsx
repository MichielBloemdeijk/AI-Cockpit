"use client";

import { useMemo, useSyncExternalStore } from "react";
import TaskDetailPage from "../../../app/(app)/tasks/[taskId]/page";
import AuthenticatedShell from "../AuthenticatedShell";

const unchanging = () => () => undefined;

/** The task id in this shell's path, or null for a malformed path (which makes no private data request). */
function readTaskId(): string | null {
  const match = /^\/tasks\/([^/]+)\/?$/.exec(window.location.pathname);
  if (!match) return null;
  try { return decodeURIComponent(match[1]); }
  catch { return null; }
}

export default function Page() {
  const taskId = useSyncExternalStore(unchanging, readTaskId, () => null);
  const params = useMemo(() => (taskId === null ? null : Promise.resolve({ taskId })), [taskId]);
  return <AuthenticatedShell>{params ? <TaskDetailPage params={params} /> : <p className="p-6">Loading task…</p>}</AuthenticatedShell>;
}
