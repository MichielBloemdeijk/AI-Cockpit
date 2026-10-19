import { afterEach, expect, it, vi } from "vitest";
import { JarvisPlaybackEvidence } from "./jarvis-playback-evidence";

afterEach(() => { vi.restoreAllMocks(); vi.useRealTimers(); });

it("records progression and mute boundaries without claiming received audio was played", async () => {
  vi.useFakeTimers(); let at = 1100;
  vi.spyOn(performance, "now").mockImplementation(() => at);
  const send = vi.fn(async (_id: string, _payload: Record<string, unknown>) => undefined);
  const element = { currentTime: 0, paused: false, muted: false, volume: 1, readyState: 4,
    srcObject: { getAudioTracks: () => [{ id: "track", readyState: "live", muted: false }] } } as unknown as HTMLAudioElement;
  const evidence = new JarvisPlaybackEvidence({ id: "s", generation: 1 }, "owner", () => element, 1000, send);
  evidence.started(1100); await evidence.sample();
  at = 1350; element.currentTime = .25; await evidence.sample();
  await vi.advanceTimersByTimeAsync(0);
  expect(send.mock.calls[0][1]).toMatchObject({ generation: 1, reports: [{ mapping_supported: true, gap: false,
    playback_start_ms: 0, playback_end_ms: 250, muted: false }] });
  at = 1600; element.currentTime = .5; element.muted = true; await evidence.sample();
  await vi.advanceTimersByTimeAsync(0);
  expect(send.mock.calls[1][1]).toMatchObject({ reports: [{ muted: true }] });
  evidence.stop();
});

it("preserves measured clock ranges and flags track replacement", async () => {
  vi.useFakeTimers(); let at = 4500; let id = "first";
  vi.spyOn(performance, "now").mockImplementation(() => at);
  const send = vi.fn(async (_id: string, _payload: Record<string, unknown>) => undefined);
  const element = { currentTime: 0, paused: false, muted: false, volume: 1, readyState: 4,
    srcObject: { getAudioTracks: () => [{ id, readyState: "live", muted: false }] } } as unknown as HTMLAudioElement;
  const evidence = new JarvisPlaybackEvidence({ id: "s", generation: 1 }, "owner", () => element, 1000, send);
  evidence.started(4500); await evidence.sample();
  at += 250; element.currentTime = .25; id = "replacement"; await evidence.sample();
  await vi.advanceTimersByTimeAsync(0);
  expect(send.mock.calls[0][1]).toMatchObject({ reports: [{ mapping_supported: true, origin_lower_ms: 1000, origin_upper_ms: 4500, gap: true }] });
  evidence.stop();
});

it("reports received media and concealment quality separately from playback", async () => {
  vi.useFakeTimers(); let at = 1100; let samples = 12000; let concealed = 0;
  vi.spyOn(performance, "now").mockImplementation(() => at);
  const send = vi.fn(async (_id: string, _payload: Record<string, unknown>) => undefined);
  const stats = async () => new Map([["audio", { type: "inbound-rtp", kind: "audio",
    totalSamplesReceived: samples, concealedSamples: concealed }]]) as unknown as RTCStatsReport;
  const element = { currentTime: 0, paused: false, muted: false, volume: 1, readyState: 4,
    srcObject: { getAudioTracks: () => [{ id: "track", readyState: "live", muted: false }] } } as unknown as HTMLAudioElement;
  const evidence = new JarvisPlaybackEvidence({ id: "s", generation: 1 }, "owner", () => element, 1000, send, stats);
  evidence.started(1100); await evidence.sample();
  at += 250; element.currentTime = .25; samples += 12000; await evidence.sample();
  await vi.advanceTimersByTimeAsync(0);
  expect(send.mock.calls[0][1]).toMatchObject({ reports: [{ received_audio: true }] });
  at += 250; element.currentTime = .5; samples += 12000; concealed += 20; await evidence.sample();
  await vi.advanceTimersByTimeAsync(0);
  expect(send.mock.calls[1][1]).toMatchObject({ reports: [{ received_audio: true, concealed_fraction: 20 / 12000 }] });
  evidence.stop();
});


it("measures microphone energy independently and does not invent silence from cached or replaced sources", async () => {
  vi.useFakeTimers(); let at = 1100; let duration = 1; let energy = 0; let id = "mic";
  vi.spyOn(performance, "now").mockImplementation(() => at);
  const send = vi.fn(async (_id: string, _payload: Record<string, unknown>) => undefined);
  const stats = async () => new Map([["input", { id, type: "media-source", kind: "audio",
    totalAudioEnergy: energy, totalSamplesDuration: duration }]]) as unknown as RTCStatsReport;
  const element = { currentTime: 0, paused: false, muted: false, volume: 1, readyState: 4,
    srcObject: { getAudioTracks: () => [{ id: "track", readyState: "live", muted: false }] } } as unknown as HTMLAudioElement;
  const evidence = new JarvisPlaybackEvidence({ id: "s", generation: 1 }, "owner", () => element, 1000, send, stats);
  evidence.started(1100); await evidence.sample();
  at += 250; element.currentTime += .25; duration += .25; await evidence.sample();
  await vi.advanceTimersByTimeAsync(0);
  expect(send.mock.calls[0][1]).toMatchObject({ reports: [{ input_source_id: "mic", input_audio_energy: 0, input_audio_duration_ms: 250 }] });
  at += 250; element.currentTime += .25; await evidence.sample();
  await vi.advanceTimersByTimeAsync(0);
  expect(send.mock.calls[1][1]).toMatchObject({ reports: [{ input_audio_energy: null, input_audio_duration_ms: null }] });
  at += 250; element.currentTime += .25; duration += .25; energy += .01; await evidence.sample();
  await vi.advanceTimersByTimeAsync(0);
  expect(send.mock.calls[2][1]).toMatchObject({ reports: [{ input_audio_energy: .01, input_audio_duration_ms: 250 }] });
  at += 250; element.currentTime += .25; duration += .25; id = "replacement"; await evidence.sample();
  await vi.advanceTimersByTimeAsync(0);
  expect(send.mock.calls[3][1]).toMatchObject({ reports: [{ input_audio_energy: null }] });
  evidence.stop();
});


it("waits for sampling to finish before scheduling another RTC poll", async () => {
  vi.useFakeTimers();
  const stats = vi.fn(async () => {
    await new Promise(resolve => setTimeout(resolve, 300));
    return new Map() as unknown as RTCStatsReport;
  });
  const element = { currentTime: 0, paused: false, muted: false, volume: 1, readyState: 4,
    srcObject: { getAudioTracks: () => [{ id: "track", readyState: "live", muted: false }] } } as unknown as HTMLAudioElement;
  const send = vi.fn(async (_id: string, _payload: Record<string, unknown>) => undefined);
  const evidence = new JarvisPlaybackEvidence({ id: "s", generation: 1 }, "owner", () => element, 1000, send, stats);
  evidence.started(1100);
  await vi.advanceTimersByTimeAsync(550);
  expect(stats).toHaveBeenCalledTimes(1);
  await vi.advanceTimersByTimeAsync(249);
  expect(stats).toHaveBeenCalledTimes(1);
  await vi.advanceTimersByTimeAsync(1);
  expect(stats).toHaveBeenCalledTimes(2);
  evidence.stop();
  await vi.advanceTimersByTimeAsync(1000);
  expect(stats).toHaveBeenCalledTimes(2);
});
