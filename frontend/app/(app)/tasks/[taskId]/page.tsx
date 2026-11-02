"use client";

import { use } from "react";
import { TaskDetailScreen } from "@/components/screens/TaskDetailScreen";

export default function TaskDetailPage({ params }: { params: Promise<{ taskId: string }> }) {
  const { taskId } = use(params);
  return <TaskDetailScreen taskId={taskId} />;
}
