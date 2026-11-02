// @vitest-environment jsdom

import { afterEach, describe, expect, it, vi } from "vitest";

const live = vi.hoisted(() => ({
  instances: [] as Array<Record<string, ReturnType<typeof vi.fn>>>,
}));
vi.mock("./live", () => ({
  JarvisLiveController: vi.fn(function JarvisLiveController(this: Record<string, unknown>) {
    const controller = {
      current: vi.fn(() => ({ status: "idle", micEnabled: false, playbackActive: false, error: null })),
      ownsSession: vi.fn(() => false),
      startWithOptions: vi.fn(async () => ({ status: "connected" })),
      stop: vi.fn(async () => undefined),
      prepare: vi.fn(async () => undefined),
      refreshPreparation: vi.fn(async () => undefined),
      recordBackendEvent: vi.fn(),
      recordLiveStreamState: vi.fn(),
    };
    live.instances.push(controller);
    return controller;
  }),
}));
const runtime = vi.hoisted(() => ({ stop: vi.fn(async () => undefined), start: vi.fn(async () => undefined) }));
vi.mock("./wake-runtime", () => ({
  JarvisWakeRuntime: vi.fn(function JarvisWakeRuntime() {
    return { current: () => ({ status: "idle", error: null }), stop: runtime.stop, start: runtime.start, rearm: vi.fn(), manualHandoff: vi.fn() };
  }),
}));
vi.mock("./wake-protocol", () => ({ configuredJarvisWakeModel: () => null }));
vi.mock("../api/jarvis", () => ({
  closeJarvisLiveSession: vi.fn(async () => undefined),
  createJarvisLiveCall: vi.fn(async () => undefined),
  submitJarvisLiveTelemetry: vi.fn(async () => 0),
}));

import { createJarvisVoiceSession, type JarvisVoiceOptions } from "./voice-session";

const options: JarvisVoiceOptions = {
  remoteAudio: () => null,
  onProviderEvent: vi.fn(),
  onError: vi.fn(),
  wakeGate: () => ({ developerMode: false, selected: "transcript_rules" }),
};

afterEach(() => {
  delete (window as Window & { JarvisPhoneVoice?: unknown }).JarvisPhoneVoice;
  live.instances = [];
  vi.clearAllMocks();
});

describe("voice session on the phone", () => {
  function installBridge() {
    const bridge = { onmessage: null as ((event: { data: string }) => void) | null, postMessage: vi.fn() };
    (window as Window & { JarvisPhoneVoice?: unknown }).JarvisPhoneVoice = bridge;
    return bridge;
  }

  it("observes Android's audio state and never stops a live conversation on dispose", () => {
    const bridge = installBridge();
    const voice = createJarvisVoiceSession(options);
    expect(voice.kind).toBe("phone");
    bridge.onmessage?.({ data: JSON.stringify({ type: "audio_state", mode: "live", text: "Listening", session_id: "s1", generation: 2 }) });
    expect(voice.current().voice.status).toBe("connected");
    expect(voice.ownsSession({ id: "s1", generation: 2 } as never)).toBe(true);

    voice.dispose();
    const commands = bridge.postMessage.mock.calls.map(([raw]) => JSON.parse(String(raw)).command);
    expect(commands).toEqual(["get_state"]);
    expect(bridge.onmessage).toBeNull();
  });
});

describe("voice session in a browser", () => {
  it("owns the Live transport and releases it on dispose", async () => {
    const voice = createJarvisVoiceSession(options);
    expect(voice.kind).toBe("browser");
    expect(voice.controllerId).toMatch(/^jarvis-browser-/);
    voice.dispose();
    await vi.waitFor(() => expect(runtime.stop).toHaveBeenCalled());
    expect(live.instances[0].stop).toHaveBeenCalled();
  });

  it("forwards durable events and stream health to its transport", () => {
    const voice = createJarvisVoiceSession(options);
    voice.recordLiveEvent({ sequence: 3 } as never);
    voice.recordStreamState("opened");
    expect(live.instances[0].recordBackendEvent).toHaveBeenCalledWith({ sequence: 3 });
    expect(live.instances[0].recordLiveStreamState).toHaveBeenCalledWith("opened");
    voice.dispose();
  });

  it("refuses to start before today's log exists", async () => {
    const voice = createJarvisVoiceSession(options);
    await expect(voice.listen(false)).rejects.toThrow("Send the first turn before arming Jarvis");
    voice.dispose();
  });
});
