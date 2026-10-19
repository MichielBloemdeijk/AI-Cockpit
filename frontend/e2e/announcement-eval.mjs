/** Standalone Playwright driver. Uses the real application and WebRTC media path. */
import { chromium } from "@playwright/test";
import { installAudioBridge, playbackQuietObserver } from "./announcement-media.mjs";
import { readFileSync, writeFileSync } from "node:fs";
import { createHash } from "node:crypto";
import { join } from "node:path";

const spec = JSON.parse(readFileSync(process.env.JARVIS_EVAL_SPEC, "utf8"));
const directory = process.env.JARVIS_EVAL_ROOT;
const token = process.env.JARVIS_EVAL_TOKEN;
const sleep = ms => new Promise(resolve => setTimeout(resolve, ms));
const utteranceId = text => createHash("sha256").update(text).digest("hex").slice(0, 16);
const speech = text => readFileSync(join(spec.corpus_directory, `${utteranceId(text)}-command.wav`)).toString("base64");
const browser = await chromium.launch({ channel: "chrome", headless: true,
  args: ["--autoplay-policy=no-user-gesture-required"] });
const context = await browser.newContext({ permissions: ["microphone"] });
const page = await context.newPage();
const evidence = { schema: "jarvis-announcement-evidence.v1", transport: "browser-webrtc",
  case_id: spec.case.id, expectation: spec.case.expectation, max_announcements: spec.case.max_announcements,
  profile: spec.profile, timing: spec.timing, target_task_ids: [], eligible_ms: {}, markers: [], sessions: [],
  recording_complete: false, playback_verified: false, clock_valid: false, input_understood: false };
const errors = [];
page.on("pageerror", e => errors.push(e.message));
let pendingSpeech = null;
let caseDeadline = Date.now() + (spec.profile === "production_A" ? 160_000 : 120_000) + (spec.case.observation_tail_ms || 0);
let currentState;
const drainPlayback = playbackQuietObserver(() => page.evaluate(() => window.__announcementEval.activity()),
  {maxMs: spec.case.observation_tail_ms || 0});

async function settle(step = {}) {
  const deadline = Date.now() + 20_000;
  while (Date.now() < deadline) {
    const s = await state();
    const activity = await page.evaluate(() => window.__announcementEval.activity());
    const started = new Set(); let lastBackendActivity = 0;
    for (const row of s.provider_events) {
      const e = row.event.type === "response.event" ? row.event.event : {};
      if (e.type) lastBackendActivity = Math.max(lastBackendActivity, row.epoch_ms);
      if (e.type === "response.created") started.add(e.response.id);
      if (["response.completed", "response.failed", "response.cancelled"].includes(e.type)) started.delete(e.response.id);
    }
    // Backend completion can precede queued Live speech. Reset the quiet interval
    // for backend activity too, so that handoff time cannot masquerade as idle.
    if (!started.size && !activity.speaking && Date.now() - Math.max(activity.lastOutputEpoch, lastBackendActivity) >= (step.quiet_ms || 1500)) {
      await mark("admission.settled", { quiet_ms: step.quiet_ms || 1500, last_backend_ms: lastBackendActivity,
        last_output_ms: activity.lastOutputEpoch, pending_responses: started.size }); return;
    }
    await sleep(100);
  }
  throw new Error("Admission setup did not settle within twenty seconds");
}

async function observeReplyTail() {
  if (!spec.case.observation_tail_ms) return;
  const result = await drainPlayback();
  if (result.extended_ms > 100 || result.exhausted) await mark("observation.playback_tail", result);
  if (result.exhausted) evidence.observation_tail_exhausted = true;
}

async function backend(path, value) {
  const response = await page.request.fetch(`${spec.backend_url}/__eval/${path}`, {
    method: value ? "POST" : "GET", headers: { "x-eval-token": token }, data: value });
  if (!response.ok()) throw new Error(`Evaluation ${path}: HTTP ${response.status()} ${await response.text()}`);
  return response.json();
}
async function mark(type, detail = {}) {
  const stamp = { type, epoch_ms: Date.now(), ...detail };
  evidence.markers.push(stamp);
  await backend("action", { action: "marker", ...stamp });
  return stamp.epoch_ms;
}
async function state() { return currentState = await backend("state"); }
async function poll(description, predicate, seconds = 25) {
  const deadline = Math.min(caseDeadline, Date.now() + seconds * 1000);
  while (Date.now() < deadline) {
    const value = await state();
    const providerError = value.provider_events.find(e => e.stream === "incoming" && e.event.type === "error");
    if (providerError) throw new Error(`Provider error: ${JSON.stringify(providerError.event)}`);
    if (predicate(value)) return value;
    await sleep(200);
  }
  throw new Error(`Timed out: ${description}`);
}
async function say(text) {
  await mark("caller.start", { text });
  const end = await page.evaluate(data => window.__announcementEval.speak(data), speech(text));
  evidence.markers.push({ type: "caller.end", epoch_ms: end, text });
  return end;
}
async function rememberSession() {
  const r = await page.request.get(`${spec.backend_url}/api/jarvis/state`);
  const s = (await r.json()).session;
  if (s && !evidence.sessions.some(x => x.id === s.id)) evidence.sessions.push({ id: s.id, generation: s.generation });
  return s;
}
async function start() {
  await page.goto(spec.frontend_url + "/jarvis", { waitUntil: "domcontentloaded" });
  await page.getByText("wake armed", { exact: true }).waitFor({ timeout: 30_000 });
  await page.evaluate(data => window.__announcementEval.speak(data), readFileSync(spec.wake_audio).toString("base64"));
  await poll("production Live readiness", s => s.sessions.some(x => x.lifecycle_state === "ready"), 45);
  await sleep(700); await rememberSession();
}
async function attachRemote() {
  return page.evaluate(() => window.__announcementEval.attachRemote());
}
function wav(chunks, rate, totalSamples) {
  const bytes = Buffer.alloc(44 + totalSamples * 2);
  bytes.write("RIFF", 0); bytes.writeUInt32LE(bytes.length - 8, 4); bytes.write("WAVEfmt ", 8);
  bytes.writeUInt32LE(16, 16); bytes.writeUInt16LE(1, 20); bytes.writeUInt16LE(1, 22);
  bytes.writeUInt32LE(rate, 24); bytes.writeUInt32LE(rate * 2, 28); bytes.writeUInt16LE(2, 32);
  bytes.writeUInt16LE(16, 34); bytes.write("data", 36); bytes.writeUInt32LE(totalSamples * 2, 40);
  for (const chunk of chunks) {
    const raw = Buffer.from(chunk.data, "base64");
    raw.copy(bytes, 44 + chunk.sample * 2, 0, Math.min(raw.length, Math.max(0, totalSamples - chunk.sample) * 2));
  }
  return bytes;
}

try {
  await context.addInitScript(installAudioBridge);
  await page.request.post(`${spec.backend_url}/api/auth/login`, { data: { password: "" } });
  await page.request.post(`${spec.backend_url}/api/jarvis/logs/new`, { data: { idempotency_key: spec.run_id } });
  const clockProbes = [];
  for (let i = 0; i < 5; i++) {
    const before = Date.now(); const s = await state(); const after = Date.now();
    clockProbes.push({ round_trip_ms: after - before, offset_ms: s.epoch_ms - (before + after) / 2 });
  }
  evidence.clock_probes = clockProbes;
  evidence.clock_valid = clockProbes.some(p => p.round_trip_ms < 200 && Math.abs(p.offset_ms) < 100);
  await start();
  const captureTimer = setInterval(() => attachRemote().catch(() => {}), 200);
  evidence.rtc_stats = [];
  let transportBusy = false;
  const transportTimer = setInterval(async () => {
    if (transportBusy) return;
    transportBusy = true;
    try { evidence.rtc_stats.push(...await page.evaluate(() => window.__announcementEval.transport())); }
    catch (error) { evidence.transport_error = error.message; }
    finally { transportBusy = false; }
  }, 200);
  try {
    if (spec.adaptive) {
      // The independent caller talks over real audio, not injected transcripts.
      const { runCaller } = await import("./announcement-caller.mjs");
      await runCaller({ page, spec, state, backend, mark, evidence });
    } else {
      for (const step of spec.case.steps) {
        if (Date.now() >= caseDeadline) throw new Error("Scenario time limit exceeded");
        if (step.type === "settle") await settle(step);
        else if (step.type === "wait_input") {
          // A fixture precondition, never a gate on application speech or delivery.
          const after = evidence.markers.filter(m => m.type === "caller.start").at(-1).epoch_ms;
          await poll("provider received caller floor instruction", s => new RegExp(step.pattern, "i").test(
            s.provider_events.filter(r => r.epoch_ms >= after && r.event.type === "session.input_transcript.delta")
              .map(r => r.event.delta || "").join("")), 12);
          await mark("caller.floor_received", { pattern: step.pattern });
        }
        else if (step.type === "observation_end") {
          await mark("observation.nominal_end");
          await observeReplyTail();
          evidence.observation_end_ms = await mark("observation.end");
        }
        else if (["observer_stop", "observer_restart"].includes(step.type)) await backend("action", { action: step.type });
        else if (step.type === "at_caller_offset") {
          const start = evidence.markers.filter(m => m.type === "caller.start").at(-1).epoch_ms;
          const due = start + step.seconds * 1000;
          if (Date.now() > due + 250) throw new Error("Floor instruction arrived after frozen completion point");
          await sleep(Math.max(0,due-Date.now()));
        }
        else if (step.type === "comparison") await mark("no_completion.comparison");
        else if (step.type === "status_query") {
          evidence.status_query_start_ms = Date.now(); evidence.status_query_end_ms = await say(step.text);
        }
        else if (step.type === "opening") await say(spec.opening);
        else if (step.type === "speak") await say(step.text);
        else if (step.type === "speak_async") pendingSpeech = say(step.text);
        else if (step.type === "join") {
          const end = await pendingSpeech; pendingSpeech = null;
          for (const id of evidence.target_task_ids) evidence.eligible_ms[id] = Math.max(evidence.eligible_ms[id] || 0, end);
        } else if (step.type === "admit") {
          let answered = false;
          const s = await poll("spoken task admission", s => {
            if (step.answer && !answered && s.tasks.length < step.count) {
              const output = s.provider_events.filter(r => r.event.type === "session.output_transcript.delta")
                .map(r => r.event.delta || "").join("");
              if (/would you|do you want|should I|shall I/i.test(output) && /update|notify|notification|tell you|completion/i.test(output)) {
                answered = true;
                // A predefined clarification answer belongs to admission only.
                pendingSpeech = say(step.answer);
              }
            }
            return s.tasks.length >= step.count;
          });
          if (pendingSpeech) { await pendingSpeech; pendingSpeech = null; }
          evidence.target_task_ids = s.tasks.slice(0, step.count).map(t => t.id);
          await rememberSession();
        } else if (step.type === "wait") await sleep(step.seconds * 1000);
        else if (step.type === "finish") {
          const id = evidence.target_task_ids[step.index || 0];
          await backend("action", { action: "finish", task_id: id, status: step.status || "completed", summary: step.summary });
          const s = await state(); evidence.eligible_ms[id] = s.tasks.find(t => t.id === id).finished_at;
          await mark("completion", { task_id: id });
        } else if (step.type === "sync") await backend("action", { action: "sync" });
        else if (step.type === "question") await backend("action", { action: "question", task_id: evidence.target_task_ids[0] });
        else if (step.type === "eligible") {
          const t = await mark("eligible");
          for (const id of evidence.target_task_ids) evidence.eligible_ms[id] = t;
        } else if (step.type === "wait_output") {
          const at = evidence.eligible_ms[evidence.target_task_ids[0]] || Date.now();
          while (Date.now() - at < 12_000) {
            const activity = await page.evaluate(() => window.__announcementEval.activity());
            if (activity.lastOutputEpoch >= at) break;
            await sleep(20);
          }
          const activity = await page.evaluate(() => window.__announcementEval.activity());
          if (activity.lastOutputEpoch < at) throw new Error("No actual completion audio before interruption");
        } else if (step.type === "disconnect") {
          await page.getByRole("button", { name: "Sleep", exact: true }).click();
          await poll("Live session disconnected before worker completion", s => s.sessions.every(x => !["connecting", "ready"].includes(x.lifecycle_state)), 10);
          await mark("session.disconnected");
        } else if (step.type === "resume") {
          await page.evaluate(data => window.__announcementEval.speak(data), readFileSync(spec.wake_audio).toString("base64"));
          await poll("replacement Live readiness", s => s.sessions.some(x => x.lifecycle_state === "ready" && !evidence.sessions.some(old => old.id === x.id)), 30);
          await rememberSession();
          await mark("session.resumed");
        } else if (step.type === "reconnect") {
          await page.getByRole("button", { name: "Sleep", exact: true }).click();
          await poll("old Live session disconnected", s => s.sessions.every(x => !["connecting", "ready"].includes(x.lifecycle_state)), 10);
          await page.getByText("wake armed", { exact: true }).waitFor({ timeout: 10_000 });
          await mark("session.disconnected");
          await page.evaluate(data => window.__announcementEval.speak(data), readFileSync(spec.wake_audio).toString("base64"));
          await poll("replacement Live readiness", s => s.sessions.some(x => x.lifecycle_state === "ready" && !evidence.sessions.some(old => old.id === x.id)), 30);
          await rememberSession();
          await mark("session.resumed");
        } else throw new Error(`Unknown scenario step ${step.type}`);
      }
    }
    await observeReplyTail();
  } finally { clearInterval(captureTimer); clearInterval(transportTimer); }
} catch (error) {
  evidence.error = error.message;
} finally {
  try {
    evidence.state = await state();
    const text = evidence.state.provider_events.filter(e => e.stream === "incoming" && e.event.type === "session.input_transcript.delta").map(e => e.event.delta || "").join(" ").toLowerCase();
    evidence.recognized_request = text;
    evidence.input_understood = /hello[\s-]+world/.test(text) && evidence.target_task_ids.length > 0;
    if (evidence.expectation === "required") evidence.input_understood &&= /tell.{0,60}(when|once)|let.{0,30}know|notify/.test(text);
    if (evidence.expectation === "forbidden") evidence.input_understood &&= /quiet|silence|silent|do not announce/.test(text);
    const capture = await page.evaluate(() => window.__announcementEval?.snapshot());
    if (capture) {
      evidence.assistant_audio_epoch_ms = capture.epoch_ms;
      evidence.recording_complete = capture.recording_complete === true && capture.context_state === "running"
        && capture.callbacks > 10 && capture.pending === 0;
      evidence.playback_verified = capture.playback.some(p => !p.paused && p.track_live && p.position > .05);
      evidence.capture = { ...capture, input: undefined, output: undefined };
      if (spec.adaptive) {
        const windows = [];
        for (const chunk of capture.input) {
          const bytes = Buffer.from(chunk.data, "base64"); let sounding = 0;
          for (let i = 0; i < bytes.length; i += 2) if (Math.abs(bytes.readInt16LE(i)) > 200) sounding++;
          if (sounding < 10) continue;
          const start = capture.epoch_ms + chunk.sample / capture.rate * 1000;
          const end = start + bytes.length / 2 / capture.rate * 1000;
          if (windows.length && start - windows.at(-1)[1] < 350) windows.at(-1)[1] = end;
          else windows.push([start, end]);
        }
        evidence.caller_audio_windows = windows;
      }
      // Acoustic intervals for every scripted run, independently of utterance pauses.
      const soundingWindows = [];
      for (const chunk of capture.input) {
        const bytes = Buffer.from(chunk.data, "base64");
        const frame = Math.max(1, Math.round(capture.rate / 100));
        for (let i = 0; i < bytes.length / 2; i += frame) {
          const count = Math.min(frame, bytes.length / 2 - i); let sounding = 0;
          for (let j = 0; j < count; j++) if (Math.abs(bytes.readInt16LE((i + j) * 2)) > 200) sounding++;
          if (sounding <= count * .05) continue;
          const a = capture.epoch_ms + (chunk.sample + i) / capture.rate * 1000;
          const b = a + count / capture.rate * 1000;
          if (soundingWindows.length && a - soundingWindows.at(-1)[1] < 80) soundingWindows.at(-1)[1] = b;
          else soundingWindows.push([a, b]);
        }
      }
      evidence.caller_voiced_windows = soundingWindows;
      writeFileSync(join(directory, "caller.wav"), wav(capture.input, capture.rate, capture.totalSamples));
      writeFileSync(join(directory, "assistant.wav"), wav(capture.output, capture.rate, capture.totalSamples));
    }
  } catch (error) { evidence.capture_error = error.message; }
  for (const session of evidence.sessions) {
    try { await page.request.delete(`${spec.backend_url}/api/jarvis/live/sessions/${session.id}?generation=${session.generation}`); }
    catch (error) { evidence.markers.push({ type: "cleanup.error", message: error.message }); }
  }
  // Catch a session created before admission failed; cleanup still belongs to this isolated host.
  try { const s = await rememberSession(); if (s) await page.request.delete(`${spec.backend_url}/api/jarvis/live/sessions/${s.id}?generation=${s.generation}`); } catch {}
  evidence.browser_errors = errors;
  writeFileSync(join(directory, "evidence.json"), JSON.stringify(evidence, null, 2));
  await browser.close();
}
if (evidence.error || evidence.capture_error) process.exitCode = 2;
