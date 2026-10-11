import { describe, expect, it, vi } from "vitest";
import { JarvisPhoneVoiceController, type PhoneVoiceBridge } from "./jarvis-phone-voice";

function fixture() {
  const messages: Record<string, unknown>[] = [];
  const bridge: PhoneVoiceBridge = { onmessage: null, postMessage: message => { messages.push(JSON.parse(message)); } };
  const snapshot = vi.fn(); const state = vi.fn();
  const controller = new JarvisPhoneVoiceController(bridge, snapshot, state);
  const update = (mode: string, extra = {}) => bridge.onmessage?.({ data: JSON.stringify({ type: "audio_state", mode, text: mode, ...extra }) });
  return { controller, bridge, messages, snapshot, state, update };
}

describe("native Android voice observer", () => {
  it("uses native commands and waits for actual readiness", async () => {
    const f = fixture(); expect(f.messages).toEqual([{ command: "get_state" }]);
    const started = f.controller.startWithOptions();
    f.update("connecting", { activation_id: "a", session_id: "s", generation: 2 });
    expect(f.controller.current().status).toBe("connecting");
    f.update("live", { activation_id: "a", session_id: "s", generation: 2 });
    expect((await started).status).toBe("connected");
    const stopped = f.controller.stop();
    expect(f.messages.at(-1)).toEqual({ command: "stop_live", activation_id: "a", session_id: "s", generation: 2 });
    f.update("paused"); await stopped; expect(f.controller.current().micEnabled).toBe(false);
    f.controller.dispose();
  });
  it("page disposal removes observers without ending native audio", async () => {
    const f = fixture(); const pending = f.controller.startWithOptions();
    f.controller.dispose(); await expect(pending).rejects.toThrow("observer closed");
    expect(f.bridge.onmessage).toBeNull();
    expect(f.messages.map(message => message.command)).toEqual(["get_state", "listen_now"]);
  });
  it("reports startup errors and ignores invalid state messages", async () => {
    const f = fixture(); const pending = f.controller.startWithOptions();
    f.update("invented"); expect(f.state).not.toHaveBeenCalled();
    f.update("error"); await expect(pending).rejects.toThrow("error"); f.controller.dispose();
  });
  it("wake readiness cannot be reported before native recording starts", async () => {
    const f = fixture(); const pending = f.controller.wake();
    f.update("wake_starting"); expect(f.controller.current().micEnabled).toBe(false);
    f.update("waiting"); await pending; f.controller.dispose();
  });
});
