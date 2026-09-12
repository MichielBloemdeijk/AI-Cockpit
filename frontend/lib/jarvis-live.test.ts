import { describe, expect, it, vi } from "vitest";

import type { JarvisRealtimeCallResponse } from "./api";
import { JarvisLiveController } from "./jarvis-live";

function fakeCall(): JarvisRealtimeCallResponse {
  return {
    role: "controller",
    session: { id: "session-1", generation: 1 } as JarvisRealtimeCallResponse["session"],
    sdp_answer: "answer",
    rotate_after_seconds: null,
    audio_policy: null,
    provider_capabilities: { continuous_conversation: true },
    server_received_at: new Date().toISOString(),
    server_responded_at: new Date().toISOString(),
  };
}

function fakeMediaStream(): MediaStream {
  const track = { stop: vi.fn() } as unknown as MediaStreamTrack;
  return {
    getAudioTracks: () => [track],
    getTracks: () => [track],
  } as unknown as MediaStream;
}

function fakePc() {
  const channel = {
    readyState: "open",
    send: vi.fn(),
    close: vi.fn(),
    onmessage: null as ((event: MessageEvent) => void) | null,
    onerror: null as (() => void) | null,
  } as unknown as RTCDataChannel;
  const pc = {
    iceGatheringState: "complete",
    localDescription: null,
    createDataChannel: vi.fn(() => channel),
    createOffer: vi.fn(async () => ({ type: "offer", sdp: "offer" } as RTCSessionDescriptionInit)),
    setLocalDescription: vi.fn(async () => undefined),
    setRemoteDescription: vi.fn(async () => undefined),
    addTrack: vi.fn(),
    close: vi.fn(),
    ontrack: null,
  } as unknown as RTCPeerConnection;
  return { channel, pc };
}

describe("JarvisLiveController", () => {
  it("negotiates oai-events before the offer and waits for session.started", async () => {
    const { pc, channel } = fakePc();
    const createCall = vi.fn(async () => fakeCall());
    const controller = new JarvisLiveController("c", {}, {
      peerConnection: () => pc,
      getUserMedia: async () => fakeMediaStream(),
      waitForBackendReady: async () => undefined,
      createCall,
    });
    const pending = controller.start();
    await vi.waitFor(() => expect(pc.createOffer).toHaveBeenCalled());
    expect(pc.createDataChannel).toHaveBeenCalledWith("oai-events");
    expect(controller.current().status).toBe("connecting");
    channel.onmessage?.({ data: JSON.stringify({ type: "session.started" }) } as MessageEvent);
    await pending;
    expect(controller.current().status).toBe("connected");
    expect(createCall).toHaveBeenCalledWith("c", "controller", "openai_live", "offer", undefined);
  });

  it("does not send raw audio or Realtime cancellation events", async () => {
    const { pc, channel } = fakePc();
    const controller = new JarvisLiveController("c", {}, {
      peerConnection: () => pc,
      getUserMedia: async () => fakeMediaStream(),
      waitForBackendReady: async () => undefined,
      createCall: async () => fakeCall(),
    });
    const pending = controller.start();
    await vi.waitFor(() => expect(channel.onmessage).toBeTypeOf("function"));
    channel.onmessage?.({ data: JSON.stringify({ type: "session.started" }) } as MessageEvent);
    await pending;
    controller.interrupt();
    const sentTypes = (channel.send as ReturnType<typeof vi.fn>).mock.calls.map(([value]) => (
      JSON.parse(String(value)) as { type?: string }
    ).type);
    expect(sentTypes).not.toContain("response.cancel");
    expect(sentTypes).not.toContain("session.input_audio.append");
  });
});
