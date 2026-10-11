"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { Settings, Bell, Cpu, Save, AudioLines } from "lucide-react";
import { ConversationSessionMetadata, getChatSettings, getJarvisPreferences, updateChatSettings, updateJarvisPreferences, getAccessSession, type AccessSession, type JarvisPreferences } from "@/lib/api";
import { enablePushNotifications, inspectPushNotifications, type PushNotificationState } from "@/lib/push-notifications";
import { LinkPhone } from "@/components/LinkPhone";
import { WorkerToolSettings } from "@/components/WorkerToolSettings";

export default function SettingsPage() {
  const [access, setAccess] = useState<AccessSession | null>(null);
  const [accessError, setAccessError] = useState("");
  const [models, setModels] = useState<string[]>([]);
  const [defaults, setDefaults] = useState<ConversationSessionMetadata | null>(null);
  const [taskAgentModel, setTaskAgentModel] = useState("");
  const [saving, setSaving] = useState(false);
  const [pushState, setPushState] = useState<PushNotificationState>("checking");
  const [jarvisPreferences, setJarvisPreferences] = useState<JarvisPreferences | null>(null);
  const [developerLabSaving, setDeveloperLabSaving] = useState(false);
  const [developerLabStatus, setDeveloperLabStatus] = useState<"saved" | "error" | null>(null);

  useEffect(() => {
    getAccessSession().then(setAccess).catch(() => setAccessError("Unable to check your connection. Reload to retry."));
    getJarvisPreferences().then(setJarvisPreferences).catch(() => {});
  }, []);

  useEffect(() => {
    if (access?.scope !== "owner") return;
    getChatSettings().then((response) => {
      setModels(response.available_models);
      setDefaults({ ...response.defaults, mode: "single" });
      setTaskAgentModel(response.task_agent_model);
    }).catch(() => {});
  }, [access?.scope]);

  useEffect(() => {
    if (access?.scope !== "owner") return;
    inspectPushNotifications()
      .then(setPushState)
      .catch(() => setPushState("error"));
  }, [access?.scope]);

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

  const handleEnablePush = async () => {
    setPushState("checking");
    try {
      setPushState(await enablePushNotifications());
    } catch {
      setPushState("error");
    }
  };

  const handleDeveloperLabChange = async (enabled: boolean) => {
    setDeveloperLabSaving(true);
    setDeveloperLabStatus(null);
    try {
      setJarvisPreferences(await updateJarvisPreferences({ developer_mode: enabled }));
      setDeveloperLabStatus("saved");
    } catch {
      setDeveloperLabStatus("error");
    } finally {
      setDeveloperLabSaving(false);
    }
  };

  const pushDescription: Record<PushNotificationState, string> = {
    checking: "Checking browser and server configuration…",
    unavailable: "This browser does not support Web Push.",
    not_configured: "Web Push is disabled or its VAPID keys are not configured on the server.",
    blocked: "Notification permission is blocked in this browser.",
    available: "Enable alerts for completed tasks and questions that need your input.",
    enabled: "Task completions and blocking questions can alert this device.",
    error: "Push notification setup could not be completed.",
  };

  return (
    <div className="flex flex-col h-full">
      <div className="flex items-center gap-2 px-4 py-3 border-b border-zinc-700 bg-zinc-900 flex-shrink-0">
        <Settings size={18} className="text-zinc-400" />
        <span className="text-sm font-medium text-zinc-100">Settings</span>
      </div>

      <div className="flex-1 overflow-y-auto p-4 md:p-6 max-w-2xl space-y-6">
        {accessError && <p role="alert">{accessError}</p>}
        {access?.scope === "device" && <section className="rounded-xl border border-emerald-800 bg-zinc-800 p-4 space-y-2">
          <h2 className="font-semibold text-emerald-300">This phone is linked</h2>
          <p className="text-sm text-zinc-300">{access.device?.label} · Connected to Jarvis. Your link stays saved through restarts.</p>
          <a href="jarvis://setup" className="block min-h-12 py-3 text-blue-400 underline">Open phone setup and status</a>
          <p className="text-xs text-zinc-400">Microphone, assistant access and notifications are set up together in the Android app.</p>
        </section>}
        <section>
          <h2 className="mb-3 flex items-center gap-2 text-sm font-semibold text-zinc-300">
            <AudioLines size={15} /> Jarvis
          </h2>
          <div className="divide-y divide-zinc-700 rounded-xl border border-zinc-700 bg-zinc-800">
            <label className="flex min-h-14 items-center justify-between gap-4 px-4 py-3 text-sm text-zinc-200">
              Voice
              <select
                value={jarvisPreferences?.voice ?? "marin"}
                disabled={!jarvisPreferences}
                onChange={(event) => void updateJarvisPreferences({ voice: event.target.value as JarvisPreferences["voice"] }).then(setJarvisPreferences)}
                className="min-h-12 rounded-xl border border-zinc-700 bg-zinc-900 px-3"
              >
                <option value="marin">Marin · feminine</option>
                <option value="cedar">Cedar · masculine</option>
              </select>
            </label>
            <label className="flex min-h-14 items-center justify-between gap-4 px-4 py-3 text-sm text-zinc-200">
              Response style
              <select
                value={jarvisPreferences?.concision ?? "concise"}
                disabled={!jarvisPreferences}
                onChange={(event) => void updateJarvisPreferences({ concision: event.target.value as JarvisPreferences["concision"] }).then(setJarvisPreferences)}
                className="min-h-12 rounded-xl border border-zinc-700 bg-zinc-900 px-3"
              >
                <option value="concise">Concise</option>
                <option value="balanced">Balanced</option>
              </select>
            </label>
            <div className="px-4 py-3 text-sm text-zinc-200" aria-describedby="developer-lab-status">
              <label className="flex min-h-8 items-center justify-between gap-4">
                <span>Developer lab</span>
                <input
                  type="checkbox"
                  checked={jarvisPreferences?.developer_mode ?? false}
                  disabled={!jarvisPreferences || developerLabSaving}
                  onChange={(event) => void handleDeveloperLabChange(event.target.checked)}
                  className="h-6 w-6 accent-violet-500"
                />
              </label>
              <p id="developer-lab-status" role="status" className="mt-1 text-xs text-zinc-500">
                {developerLabSaving ? "Saving developer lab setting…" : developerLabStatus === "saved" ? "Saved. Developer lab access is persistent." : developerLabStatus === "error" ? "Could not save this setting. Try again." : jarvisPreferences ? (jarvisPreferences.developer_mode ? "Enabled" : "Disabled") : "Loading…"}
              </p>
            </div>
            <Link href="/settings/wake" className="flex min-h-14 items-center justify-between px-4 py-3 text-sm text-zinc-200">
              Wake-word calibration <span className="text-xs text-zinc-500">Open wizard →</span>
            </Link>
            {access?.scope === "owner" && <LinkPhone />}
          </div>
        </section>

        {access?.scope === "owner" && <>
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

        <section>
          <h2 className="mb-3 flex items-center gap-2 text-sm font-semibold text-zinc-300">
            <Bell size={15} />
            Task notifications
          </h2>
          <div className="rounded-xl border border-zinc-700 bg-zinc-800 px-4 py-4">
            <div className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
              <div>
                <div className="text-sm font-medium text-zinc-100">
                  {pushState === "enabled" ? "Notifications enabled" : "Web Push"}
                </div>
                <p className="mt-1 text-xs text-zinc-500">{pushDescription[pushState]}</p>
              </div>
              {pushState === "available" || pushState === "error" ? (
                <button
                  type="button"
                  onClick={() => void handleEnablePush()}
                  className="rounded-xl bg-blue-600 px-4 py-2 text-sm font-medium text-white"
                >
                  Enable notifications
                </button>
              ) : null}
            </div>
          </div>
          <p className="mt-2 text-xs text-zinc-500">
            Ordinary progress stays in the app. Only terminal task states and questions that block work create Push alerts.
          </p>
        </section>
        <WorkerToolSettings />
        </>}
      </div>
    </div>
  );
}
