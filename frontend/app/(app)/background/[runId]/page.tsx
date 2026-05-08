"use client";

import Link from "next/link";
import { ArrowLeft, Bot } from "lucide-react";

export default function BackgroundRunDetailPage({ params }: { params: Promise<{ runId: string }> }) {
  void params;

  return (
    <div className="flex h-full items-center justify-center px-6">
      <div className="max-w-xl text-center">
        <div className="mx-auto mb-4 flex h-16 w-16 items-center justify-center rounded-2xl bg-zinc-800">
          <Bot size={28} className="text-zinc-500" />
        </div>
        <h1 className="text-xl font-semibold text-zinc-100">Background runs are disabled</h1>
        <p className="mt-3 text-sm text-zinc-400">
          This route is intentionally a placeholder while app work is fully chat-first.
        </p>
        <Link href="/background" className="mt-5 inline-flex items-center gap-2 rounded-xl border border-zinc-800 bg-zinc-900 px-4 py-2 text-sm text-zinc-200 transition-colors hover:border-zinc-700 hover:bg-zinc-800">
          <ArrowLeft size={14} />
          Back to background
        </Link>
      </div>
    </div>
  );
}