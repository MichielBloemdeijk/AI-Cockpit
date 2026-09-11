export type JarvisWakeGateMode = "transcript_rules" | "immediate";

export const JARVIS_WAKE_GATE_STORAGE_KEY = "jarvis.developer.wakeGateMode.v1";

export function isJarvisWakeGateMode(value: unknown): value is JarvisWakeGateMode {
  return value === "transcript_rules" || value === "immediate";
}

/** Read the developer selection without allowing malformed local storage to change behavior. */
export function loadJarvisWakeGateMode(
  storage: Pick<Storage, "getItem"> | null | undefined = (
    typeof window === "undefined" ? undefined : window.localStorage
  ),
): JarvisWakeGateMode {
  if (!storage) return "transcript_rules";
  try {
    const value = storage.getItem(JARVIS_WAKE_GATE_STORAGE_KEY);
    return isJarvisWakeGateMode(value) ? value : "transcript_rules";
  } catch {
    return "transcript_rules";
  }
}

export function saveJarvisWakeGateMode(
  mode: JarvisWakeGateMode,
  storage: Pick<Storage, "setItem"> | null | undefined = (
    typeof window === "undefined" ? undefined : window.localStorage
  ),
): void {
  if (!storage || !isJarvisWakeGateMode(mode)) return;
  try {
    storage.setItem(JARVIS_WAKE_GATE_STORAGE_KEY, mode);
  } catch {
    // Storage can be unavailable in private or embedded browser contexts.
  }
}

/** Developer Lab is the only authority allowed to select immediate mode. */
export function resolveJarvisWakeGateMode(
  developerMode: boolean,
  requested: JarvisWakeGateMode,
): JarvisWakeGateMode {
  return developerMode && requested === "immediate" ? "immediate" : "transcript_rules";
}
