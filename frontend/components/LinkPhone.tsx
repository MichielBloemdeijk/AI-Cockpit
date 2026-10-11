"use client";

import { useEffect, useState } from "react";
import { createJarvisPairingCode, getLinkedDevices, unlinkDevice, type JarvisPairingCode, type LinkedDevice } from "@/lib/api";

export function LinkPhone() {
  const [link, setLink] = useState<JarvisPairingCode | null>(null);
  const [devices, setDevices] = useState<LinkedDevice[]>([]);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [now, setNow] = useState(Date.now());
  const [linkedIds, setLinkedIds] = useState<string[]>([]);
  useEffect(() => {
    void getLinkedDevices().then(setDevices).catch(() => {});
    const timer = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(timer);
  }, []);
  useEffect(() => {
    const refresh = () => void getLinkedDevices().then(next => {
      if (link && next.some(device => !linkedIds.includes(device.id))) setLink(null);
      setDevices(next);
    }).catch(() => {});
    window.addEventListener("focus", refresh);
    const timer = link ? setInterval(refresh, 5000) : undefined;
    return () => { window.removeEventListener("focus", refresh); if (timer) clearInterval(timer); };
  }, [link, linkedIds]);
  const expired = !!link && Date.parse(link.expires_at) <= now;
  async function generate() {
    setBusy(true); setError("");
    try { const current = await getLinkedDevices(); setDevices(current); setLinkedIds(current.map(device => device.id)); setLink(await createJarvisPairingCode()); }
    catch (failure) { setError(failure instanceof Error ? failure.message : "Unable to create link."); }
    finally { setBusy(false); }
  }
  return <section className="space-y-3 rounded-xl border border-zinc-800 p-4">
    <h2 className="font-semibold">Link phone</h2>
    <p className="text-sm text-zinc-400">Connect Tailscale on your phone. Install Jarvis, then scan or open a link. Your phone stays linked through restarts.</p>
    <a className="block text-blue-400 underline" href="/api/jarvis/devices/android.apk">Download Android app</a>
    <button disabled={busy} className="rounded-lg bg-blue-600 px-4 py-2 disabled:opacity-50" onClick={generate}>{busy ? "Creating link…" : "Link phone"}</button>
    {link && !expired && <div className="space-y-2">
      {link.qr_svg && <img alt="Scan to link Jarvis" width={240} height={240} src={`data:image/svg+xml;charset=utf-8,${encodeURIComponent(link.qr_svg)}`} className="bg-white p-2" />}
      {link.link ? <a className="block text-blue-400 underline" href={link.link} target="_blank" rel="noreferrer">Open on phone</a> : <p>Set JARVIS_PUBLIC_URL in the private configuration to show a QR link.</p>}
      <p className="break-all text-sm">Address: {link.server_url || "Your Jarvis address"}</p>
      <p>Code: <strong className="font-mono tracking-widest">{link.code}</strong></p>
      <p className="text-sm text-zinc-400">One use · expires {new Date(link.expires_at).toLocaleTimeString()}</p>
    </div>}
    {expired && <p>Link expired. Select Link phone to create another.</p>}
    {error && <p role="alert" className="text-red-400">{error}</p>}
    {devices.map(device => <div key={device.id} className="flex items-center justify-between gap-3"><span>{device.label} · Linked</span><button className="text-red-400 underline" onClick={async () => {
      try { await unlinkDevice(device.id); setDevices(await getLinkedDevices()); }
      catch (failure) { setError(failure instanceof Error ? failure.message : "Unable to unlink."); }
    }}>Unlink</button></div>)}
  </section>;
}
