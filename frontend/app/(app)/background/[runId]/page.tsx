import { permanentRedirect } from "next/navigation";

export default async function BackgroundRunDetailPage({ params }: { params: Promise<{ runId: string }> }) {
  const { runId } = await params;
  permanentRedirect(`/tasks/${encodeURIComponent(runId)}`);
}
