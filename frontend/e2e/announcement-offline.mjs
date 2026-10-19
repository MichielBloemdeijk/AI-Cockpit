/** Network-blocked recording/clock/cleanup exercise before provider screening. */
import { createServer } from "node:http";
import assert from "node:assert/strict";
import { chromium } from "@playwright/test";
import { installAudioBridge, waitForPlaybackQuiet, playbackQuietObserver } from "./announcement-media.mjs";

let virtualNow = 10000;
const quiet = await waitForPlaybackQuiet(async () => ({lastOutputEpoch: virtualNow < 14000 ? virtualNow : 14000}),
  {now: () => virtualNow, pause: async ms => { virtualNow += ms; }});
assert.equal(quiet.extended_ms, 6000);
assert.equal(quiet.exhausted, false);
const busy = await waitForPlaybackQuiet(async () => ({lastOutputEpoch: virtualNow}),
  {maxMs: 300, now: () => virtualNow, pause: async ms => { virtualNow += ms; }});
assert.equal(busy.exhausted, true);
assert.equal(busy.extended_ms, 300);
const shared = playbackQuietObserver(async () => ({lastOutputEpoch: virtualNow}),
  {maxMs: 300, now: () => virtualNow, pause: async ms => { virtualNow += ms; }});
assert.equal((await shared()).extended_ms, 300);
assert.equal((await shared()).extended_ms, 0);

const server = createServer((request, response) => {
  response.writeHead(200, { "Content-Type": "text/html" });
  response.end("<!doctype html><audio autoplay></audio>");
});
await new Promise(resolve => server.listen(0, "127.0.0.1", resolve));
const origin = `http://127.0.0.1:${server.address().port}`;
const browser = await chromium.launch({ channel: "chrome", headless: true,
  args: ["--autoplay-policy=no-user-gesture-required"] });
let duplexTimingMs;
try {
  const context = await browser.newContext({ permissions: ["microphone"] });
  await context.route("**/*", route => route.request().url().startsWith(origin) ? route.continue() : route.abort());
  await context.addInitScript(installAudioBridge);
  const page = await context.newPage(); await page.goto(origin);
  await page.evaluate(async () => {
    const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
    const element = document.querySelector("audio"); element.srcObject = stream; await element.play();
    window.__announcementEval.attachRemote();
  });
  await page.waitForTimeout(450);
  const silence = await page.evaluate(() => window.__announcementEval.snapshot());
  assert(silence.callbacks > 4 && silence.context_state === "running");
  assert(silence.input.every(c => Buffer.from(c.data, "base64").every(b => b === 0)));
  const signal = Buffer.alloc(24000 * 2);
  for (let i = 0; i < 24000; i++) signal.writeInt16LE(Math.round(Math.sin(i / 10) * 5000), i * 2);
  await page.evaluate(data => window.__announcementEval.enqueuePCM(data), signal.toString("base64"));
  await page.waitForTimeout(1500);
  const audio = await page.evaluate(() => window.__announcementEval.snapshot());
  assert(audio.recorder === "audio_worklet_v1" && audio.recording_complete);
  assert(audio.pending === 0 && audio.output.some(c => Buffer.from(c.data, "base64").some(b => b !== 0)));
  assert(audio.playback.some(p => !p.paused && p.position > .1));
  assert(audio.playback.every(p => p.audio_end_ms > p.audio_start_ms && p.track_id && p.volume === 1 && p.muted === false));
  await page.evaluate(() => { document.querySelector("audio").muted = true; });
  await page.waitForTimeout(100);
  const muted = await page.evaluate(() => window.__announcementEval.snapshot());
  assert(muted.playback.at(-1).muted === true);
  await page.evaluate(() => { document.querySelector("audio").muted = false; document.querySelector("audio").pause(); });
  await page.waitForTimeout(100);
  const paused = await page.evaluate(() => window.__announcementEval.snapshot());
  assert(paused.playback.at(-1).paused === true);
  assert(audio.input.every((c, i, all) => i === 0 || c.sample > all[i - 1].sample));
  // Reproduce the main-thread stalls that dropped ScriptProcessor callbacks.
  // Worklet frames must remain contiguous while DOM/JS is unavailable.
  await page.evaluate(data => window.__announcementEval.enqueuePCM(data), signal.toString("base64"));
  await page.evaluate(() => { const end = performance.now() + 750; while (performance.now() < end) {} });
  await page.waitForTimeout(600);
  const stressed = await page.evaluate(() => window.__announcementEval.snapshot());
  assert(stressed.recording_complete && stressed.errors.length === 0);
  for (const key of ["input", "output"]) {
    const chunks = stressed[key];
    assert(chunks.every((c,i) => !i || c.sample === chunks[i-1].sample + Buffer.from(chunks[i-1].data,"base64").length/2),
      `${key} lost audio while the main thread was blocked`);
  }
  // Calibrate the recorder over genuine duplex WebRTC, independently of ASR.
  const duplex = await page.evaluate(async () => {
    const caller = new RTCPeerConnection(), receiver = new RTCPeerConnection();
    const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
    caller.addTrack(stream.getAudioTracks()[0], stream);
    const ready = new Promise(resolve => receiver.ontrack = async e => {
      const element = document.querySelector("audio"); element.srcObject = e.streams[0]; await element.play();
      window.__announcementEval.attachRemote(); resolve();
    });
    caller.onicecandidate = e => e.candidate && receiver.addIceCandidate(e.candidate);
    receiver.onicecandidate = e => e.candidate && caller.addIceCandidate(e.candidate);
    await caller.setLocalDescription(await caller.createOffer());
    await receiver.setRemoteDescription(caller.localDescription);
    await receiver.setLocalDescription(await receiver.createAnswer());
    await caller.setRemoteDescription(receiver.localDescription); await ready;
    window.__duplexPeers = [caller, receiver];
    return true;
  });
  assert(duplex);
  await page.waitForTimeout(400);
  await page.evaluate(data => window.__announcementEval.enqueuePCM(data), signal.toString("base64"));
  await page.waitForTimeout(1400);
  const wire = await page.evaluate(() => window.__announcementEval.transport());
  assert(wire.some(p => p.reports.some(s => s.type === "outbound-rtp" && s.packetsSent > 10)));
  assert(wire.some(p => p.reports.some(s => s.type === "inbound-rtp" && s.packetsReceived > 10)));
  const calibrated = await page.evaluate(() => window.__announcementEval.snapshot());
  const sounding = key => calibrated[key].filter(c => {
    const b = Buffer.from(c.data, "base64");
    for (let i = 0; i < b.length; i += 2) if (Math.abs(b.readInt16LE(i)) > 200) return true;
    return false;
  });
  const outgoing = sounding("input").at(-1).sample, incoming = sounding("output").at(-1).sample;
  duplexTimingMs = (incoming - outgoing) / calibrated.rate * 1000;
  assert(Math.abs(incoming - outgoing) / calibrated.rate < .25, "Duplex timing calibration exceeds tolerance");
  await page.evaluate(() => window.__duplexPeers.forEach(p => p.close()));
  await page.evaluate(async () => { await navigator.mediaDevices.getUserMedia({ audio: true }); });
  await page.waitForTimeout(200);
  const reconnect = await page.evaluate(() => window.__announcementEval.snapshot());
  assert(reconnect.replaced_streams === 2 && reconnect.epoch_ms === silence.epoch_ms);
  assert(reconnect.output.length >= audio.output.length && reconnect.callbacks > 0);
  console.log(JSON.stringify({ status: "passed", continuous_silence: true, real_playback: true,
    reconnect_retention: true, main_thread_stall_ms:750, frame_coverage_contiguous:true,
    duplex_timing_ms: duplexTimingMs, callback_count: reconnect.callbacks }));
} finally {
  await browser.close(); await new Promise(resolve => server.close(resolve));
}
