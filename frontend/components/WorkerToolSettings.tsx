"use client";

import { useEffect, useState } from "react";
import { getWorkerToolSettings, saveWorkerToolSettings, type NativeToolGroup, type WorkerToolSettings as Settings } from "@/lib/api";

const labels: Record<NativeToolGroup, string> = {
  "native.local": "Files, commands and tests",
  "native.research": "Web research",
  "native.subagents": "Subagents",
  "native.skills": "Approved skills",
};

export function WorkerToolSettings() {
  const [settings, setSettings] = useState<Settings | null>(null);
  const [status, setStatus] = useState("");
  const [busy, setBusy] = useState(false);
  useEffect(() => { getWorkerToolSettings().then(setSettings).catch(() => setStatus("Unable to load worker tool settings.")); }, []);
  const toggle = (group: NativeToolGroup, field: "defaults" | "disabled", checked: boolean) => {
    setSettings(current => current && ({ ...current, policy: { ...current.policy,
      [field]: checked ? [...current.policy[field], group] : current.policy[field].filter(item => item !== group) } }));
  };
  const save = async () => {
    if (!settings || busy) return;
    setBusy(true);
    try {
      const saved = await saveWorkerToolSettings(settings);
      setSettings({ ...settings, ...saved });
      setStatus("Saved. Default selections apply to new runs; restrictions also stop affected active runs.");
    } catch (error) { setStatus(error instanceof Error ? error.message : "Unable to save."); }
    finally { setBusy(false); }
  };
  return <section className="space-y-3 rounded-xl border border-zinc-700 bg-zinc-900 p-4 text-sm text-zinc-200">
    <h2 className="font-semibold">Worker tools</h2>
    <p className="text-xs text-zinc-400">Workflows and tasks can adjust the defaults. Disabled groups cannot be restored by a task. Local tools require the Windows boundary and workload checks to pass.</p>
    {settings?.local_execution_mode === "trusted" && <p className="text-xs text-amber-300">Setup selected the trusted command broker. File edits stay within assigned folders; commands inherit your Windows user&apos;s access. Native local tools remain gated until their workload checks pass.</p>}
    {settings?.groups.map(group => <div key={group} className="flex flex-wrap items-center justify-between gap-3 border-t border-zinc-800 py-3">
      <span>{labels[group]}</span>
      <div className="flex gap-4">
        <label className="flex gap-2"><input type="checkbox" disabled={busy} checked={settings.policy.defaults.includes(group)} onChange={event => toggle(group, "defaults", event.target.checked)} />Default</label>
        <label className="flex gap-2"><input type="checkbox" disabled={busy} checked={settings.policy.disabled.includes(group)} onChange={event => toggle(group, "disabled", event.target.checked)} />Disabled</label>
      </div>
    </div>)}
    {settings && <>
      <label className="flex items-center gap-2"><input type="checkbox" disabled={busy} checked={settings.policy.command_network} onChange={event => setSettings({ ...settings, policy: { ...settings.policy, command_network: event.target.checked } })} />Allow network access for native commands</label>
      <label className="block">Approved skill files (one absolute SKILL.md path per line)
        <textarea className="mt-2 block min-h-20 w-full rounded border border-zinc-700 bg-zinc-950 p-2" disabled={busy} value={settings.policy.approved_skills.join("\n")} onChange={event => setSettings({ ...settings, policy: { ...settings.policy, approved_skills: event.target.value.split("\n").map(value => value.trim()).filter(Boolean) } })} />
      </label>
      <button onClick={() => void save()} disabled={busy} className="rounded bg-blue-600 px-4 py-2 disabled:opacity-40">{busy ? "Saving…" : "Save worker tools"}</button>
    </>}
    <p role="status" className="text-xs text-zinc-400">{status}</p>
  </section>;
}
