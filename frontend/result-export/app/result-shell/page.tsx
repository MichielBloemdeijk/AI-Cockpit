"use client";

import { useEffect, useState } from "react";
import ResultScreen from "../../../app/results/ResultScreen";

export default function ResultShell() {
  const [publicId, setPublicId] = useState<string | null>(null);
  useEffect(() => {
    const match = /^\/results\/([^/]+)\/?$/.exec(window.location.pathname);
    if (match) setPublicId(decodeURIComponent(match[1]));
  }, []);
  return publicId ? <ResultScreen publicId={publicId} /> : <main className="p-8" role="status">Loading result…</main>;
}
