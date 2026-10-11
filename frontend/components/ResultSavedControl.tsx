"use client";

import { useState } from "react";
import { setResultSaved, type ResultAction } from "@/lib/api";

export default function ResultSavedControl({ publicId, action, refresh }: {
  publicId: string; action: ResultAction | undefined; refresh: () => void;
}) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  if (!action || action.id !== "set_saved") return null;
  const toggle = async () => {
    setBusy(true); setError(null);
    try { await setResultSaved(publicId, !action.value.saved, action.revision); }
    catch (cause) {
      setError(cause instanceof Error && cause.message.includes("409") ? "Saved state changed elsewhere. Please try again." : "Unable to change saved state.");
    } finally { refresh(); setBusy(false); }
  };
  return <div>
    <button type="button" aria-pressed={action.value.saved} disabled={busy} onClick={() => void toggle()}
      className="rounded-lg border border-zinc-600 px-3 py-2 text-sm hover:bg-zinc-800 disabled:opacity-50">
      {action.value.saved ? "Saved" : "Save"}
    </button>
    {error && <p role="alert" className="mt-2 text-sm text-amber-300">{error}</p>}
  </div>;
}
