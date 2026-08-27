import { redirect } from "next/navigation";

export default async function BackgroundRunDetailPage({ params }: { params: Promise<{ runId: string }> }) {
  const { runId } = await params;
  redirect(`/tasks/${encodeURIComponent(runId)}`);
}
