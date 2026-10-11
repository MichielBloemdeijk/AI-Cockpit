"use client";

import type { NativeToolGroup, ToolOverrides } from "@/lib/api";

const groups: [NativeToolGroup, string][] = [
  ["native.local", "Local coding"], ["native.research", "Web research"],
  ["native.subagents", "Subagents"], ["native.skills", "Approved skills"],
];

export function TaskToolOverrides({ value, onChange }: { value?: ToolOverrides; onChange: (value: ToolOverrides) => void }) {
  return <details className="mt-3 text-xs text-zinc-400">
    <summary className="cursor-pointer">Tool choices for this task</summary>
    <p className="mt-2">Inherit the workflow defaults, or choose tools for the next run. Globally disabled tools remain unavailable.</p>
    <div className="mt-2 grid gap-2 sm:grid-cols-2">{groups.map(([id, label]) => <label key={id} className="flex items-center justify-between gap-3">
      {label}<select aria-label={label} className="rounded border border-zinc-700 bg-zinc-950 p-1"
        value={value?.add.includes(id) ? "add" : value?.remove.includes(id) ? "remove" : "inherit"}
        onChange={event => {
          const next = { add: (value?.add ?? []).filter(group => group !== id), remove: (value?.remove ?? []).filter(group => group !== id) };
          if (event.target.value === "add") next.add.push(id);
          if (event.target.value === "remove") next.remove.push(id);
          onChange(next);
        }}><option value="inherit">Inherit</option><option value="add">Add</option><option value="remove">Remove</option></select>
    </label>)}</div>
  </details>;
}
