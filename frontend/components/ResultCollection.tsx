"use client";

import Link from "@/components/CockpitLink";
import { loginReturnUrl } from "@/lib/navigation";
import { useCallback, useEffect, useRef, useState } from "react";
import { checkAuth, listCollectionResults, type ResultCard } from "@/lib/api";
import ResultSavedControl from "./ResultSavedControl";

export default function ResultCollection({ slug, title }: { slug: string; title: string }) {
  const [draft, setDraft] = useState("");
  const [search, setSearch] = useState("");
  const [order, setOrder] = useState("updated");
  const [cards, setCards] = useState<ResultCard[]>([]);
  const [cursor, setCursor] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const generation = useRef(0);
  const load = useCallback(async (after?: string) => {
    const version = ++generation.current;
    setBusy(true); setError(null);
    try {
      const page = await listCollectionResults(slug, { search, order, ...(after ? { cursor: after } : {}) });
      if (version !== generation.current) return;
      setCards(previous => after ? [...previous, ...page.items.filter(item => !previous.some(card => card.public_id === item.public_id))] : page.items);
      setCursor(page.next_cursor);
    } catch (cause) { if (version === generation.current) setError(cause instanceof Error ? cause.message : "Unable to load collection"); }
    finally { if (version === generation.current) setBusy(false); }
  }, [slug, search, order]);
  useEffect(() => {
    let disposed = false;
    void checkAuth().then(ok => {
      if (disposed) return;
      if (ok) void load();
      else window.location.replace(loginReturnUrl());
    }).catch(() => { if (!disposed) setError("Unable to check your session."); });
    return () => { disposed = true; ++generation.current; };
  }, [load]);
  return <main className="mx-auto min-h-screen max-w-4xl px-4 py-8 text-zinc-100">
    <Link href="/workspace/apps" className="text-sky-400">← Apps</Link>
    <h1 className="my-6 text-3xl font-semibold">{title}</h1>
    <form className="mb-6 flex flex-wrap gap-3" onSubmit={event => { event.preventDefault(); setSearch(draft); }}>
      <label className="flex-1">Search results<input value={draft} onChange={event => setDraft(event.target.value)} className="mt-1 block w-full rounded-lg border border-zinc-600 bg-zinc-900 p-2" /></label>
      <label>Order<select value={order} onChange={event => setOrder(event.target.value)} className="mt-1 block rounded-lg border border-zinc-600 bg-zinc-900 p-2">
        <option value="updated">Recently updated</option><option value="title">Title</option>
      </select></label>
      <button type="submit" className="self-end rounded-lg bg-blue-600 px-4 py-2">Search</button>
    </form>
    {error && <p role="alert" className="mb-4 text-amber-300">{error}</p>}
    {!busy && !cards.length && <p>No results yet.</p>}
    <ul className="space-y-4">{cards.map(card => <li key={card.public_id} className="rounded-xl border border-zinc-700 bg-zinc-900 p-4">
      <div className="flex items-start justify-between gap-4"><h2 className="text-xl font-medium"><Link href={card.url} className="text-sky-400">{card.title}</Link></h2>
        <ResultSavedControl publicId={card.public_id} action={card.actions?.find(action => action.id === "set_saved")} refresh={() => void load()} /></div>
      {card.summary && <p className="mt-2 text-zinc-400">{card.summary}</p>}
      <p className="mt-2 text-sm text-zinc-400">{card.progress_label || card.status}</p>
      {card.warning && <p className="mt-2 text-sm text-amber-300">{card.warning}</p>}
    </li>)}</ul>
    {busy && <p role="status" className="mt-4">Loading results…</p>}
    {cursor && <button disabled={busy} onClick={() => void load(cursor)} className="mt-6 rounded-lg border border-zinc-600 px-4 py-2 disabled:opacity-50">Load more</button>}
  </main>;
}
