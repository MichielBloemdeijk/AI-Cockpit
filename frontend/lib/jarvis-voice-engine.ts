import type { JarvisVoiceEngine } from "./jarvis-voice-client";

const KEY = "jarvis.voice_engine";
export function loadJarvisVoiceEngine(): JarvisVoiceEngine {
  if (typeof window === "undefined") return "realtime";
  return window.localStorage.getItem(KEY) === "openai_live" ? "openai_live" : "realtime";
}
export function saveJarvisVoiceEngine(engine: JarvisVoiceEngine): void { window.localStorage.setItem(KEY, engine); }
