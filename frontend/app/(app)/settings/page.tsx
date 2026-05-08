"use client";

import { useEffect, useState } from "react";
import { Settings, Cpu, Save } from "lucide-react";
import { ConversationSessionMetadata, getChatSettings, updateChatSettings } from "@/lib/api";

export default function SettingsPage() {
  const [models, setModels] = useState<string[]>([]);
  const [defaults, setDefaults] = useState<ConversationSessionMetadata | null>(null);
  const [taskAgentModel, setTaskAgentModel] = useState("");
  const [saving, setSaving] = useState(false);

  useEffect(() => {
    getChatSettings().then((response) => {
      setModels(response.available_models);
      setDefaults({ ...response.defaults, mode: "single" });
      setTaskAgentModel(response.task_agent_model);
    }).catch(() => {});
  }, []);

  const toggleCouncilModel = (model: string) => {
    setDefaults((current) => {
      if (!current) return current;
      const exists = current.council_models.includes(model);
      const nextModels = exists
        ? current.council_models.filter((item) => item !== model)
        : [...current.council_models, model];
      if (nextModels.length === 0) {
        return current;
      }
      return { ...current, council_models: nextModels };
    });
  };

  const handleSave = async () => {
    if (!defaults || !taskAgentModel) return;
    setSaving(true);
    try {
      const updated = await updateChatSettings({ defaults: { ...defaults, mode: "single" }, task_agent_model: taskAgentModel });
      setDefaults({ ...updated.defaults, mode: "single" });
      setModels(updated.available_models);
      setTaskAgentModel(updated.task_agent_model);
    } finally {
      setSaving(false);
    }
  };

  return (
    <div className="flex flex-col h-full">
      <div className="flex items-center gap-2 px-4 py-3 border-b border-zinc-700 bg-zinc-900 flex-shrink-0">
        <Settings size={18} className="text-zinc-400" />
        <span className="text-sm font-medium text-zinc-100">Settings</span>
      </div>

      <div className="flex-1 overflow-y-auto p-4 md:p-6 max-w-2xl space-y-6">
        {/* LLM Models */}
        <section>
          <h2 className="text-sm font-semibold text-zinc-300 mb-3 flex items-center gap-2">
            <Cpu size={15} />
            Chat models
          </h2>
          <div className="rounded-xl border border-zinc-700 bg-zinc-800 divide-y divide-zinc-700">
            <div className="px-4 py-3">
              <div className="text-xs text-zinc-500 mb-2">Default agent chat model</div>
              <select
                value={defaults?.single_model ?? ""}
                onChange={(e) => defaults && setDefaults({ ...defaults, single_model: e.target.value })}
                className="w-full rounded-lg border border-zinc-700 bg-zinc-900 px-3 py-2 text-sm text-zinc-100"
              >
                {models.map((model) => (
                  <option key={model} value={model}>{model}</option>
                ))}
              </select>
            </div>
            <div className="px-4 py-3">
              <div className="text-xs text-zinc-500 mb-2">Default council models</div>
              <div className="flex flex-wrap gap-2">
                {models.map((model) => {
                  const selected = defaults?.council_models.includes(model);
                  return (
                    <button
                      key={model}
                      onClick={() => toggleCouncilModel(model)}
                      className={`rounded-full border px-3 py-1.5 text-xs ${selected ? "border-blue-500/40 bg-blue-500/10 text-blue-300" : "border-zinc-700 text-zinc-400"}`}
                    >
                      {model}
                    </button>
                  );
                })}
              </div>
            </div>
            <div className="px-4 py-3">
              <div className="text-xs text-zinc-500 mb-2">Default synthesizer model</div>
              <select
                value={defaults?.synthesizer_model ?? ""}
                onChange={(e) => defaults && setDefaults({ ...defaults, synthesizer_model: e.target.value })}
                className="w-full rounded-lg border border-zinc-700 bg-zinc-900 px-3 py-2 text-sm text-zinc-100"
              >
                {models.map((model) => (
                  <option key={model} value={model}>{model}</option>
                ))}
              </select>
            </div>
            <div className="px-4 py-3">
              <div className="text-xs text-zinc-500 mb-2">Default agent loop model</div>
              <select
                value={taskAgentModel}
                onChange={(e) => setTaskAgentModel(e.target.value)}
                className="w-full rounded-lg border border-zinc-700 bg-zinc-900 px-3 py-2 text-sm text-zinc-100"
              >
                {models.map((model) => (
                  <option key={model} value={model}>{model}</option>
                ))}
              </select>
            </div>
          </div>
          <p className="text-xs text-zinc-500 mt-2">
            These defaults apply only to future sessions. Existing conversations keep their stored session metadata.
          </p>
          <button
            onClick={handleSave}
            disabled={!defaults || saving}
            className="mt-4 inline-flex items-center gap-2 rounded-xl bg-blue-600 px-4 py-2 text-sm font-medium text-white disabled:opacity-50"
          >
            <Save size={15} />
            {saving ? "Saving..." : "Save defaults"}
          </button>
        </section>
      </div>
    </div>
  );
}
