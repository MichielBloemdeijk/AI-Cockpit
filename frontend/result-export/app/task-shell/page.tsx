"use client";

import { useSyncExternalStore } from "react";
import { TaskDetailScreen } from "@/components/screens/TaskDetailScreen";
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
  return <AuthenticatedShell>{taskId !== null ? <TaskDetailScreen taskId={taskId} /> : <p className="p-6">Loading task…</p>}</AuthenticatedShell>;
}
