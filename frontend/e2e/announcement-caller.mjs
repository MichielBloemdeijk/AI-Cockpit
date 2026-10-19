/** Independent duplex caller. Only agenda and microphone audio cross its boundary. */
import { spawn } from "node:child_process";
import { createInterface } from "node:readline";
import { appendFileSync, writeFileSync } from "node:fs";
import { join } from "node:path";

export async function runCaller({ page, spec, state, backend, mark, evidence }) {
  const root = process.env.JARVIS_EVAL_ROOT;
  const child = spawn(spec.python, ["-m", "app.announcement_eval.caller"], {
    cwd: spec.backend_directory, windowsHide: true,
    env: { ...process.env, JARVIS_CALLER_AGENDA: JSON.stringify(spec.adaptive.agenda) },
    stdio: ["pipe", "pipe", "pipe"] });
  let ready = false, closed = false, error, pending = Promise.resolve();
  const lines = createInterface({ input: child.stdout });
  child.stderr.on("data", data => appendFileSync(join(root, "caller.log"), data));
  child.on("error", e => error = e);
  lines.on("line", line => {
    let event;
    try { event = JSON.parse(line); } catch { error = new Error("Invalid caller relay event"); return; }
    if (event.type === "session.output_audio.delta") {
      pending = pending.then(() => page.evaluate(audio => window.__announcementEval.enqueuePCM(audio), event.delta));
      pending.catch(e => error = e);
    } else {
      appendFileSync(join(root, "caller-events.jsonl"), JSON.stringify({ epoch_ms: Date.now(), event }) + "\n");
    }
    if (event.type === "session.started") ready = true;
    if (event.type === "session.closed") closed = true;
    if (event.type === "error") error = new Error("Caller provider error: " + JSON.stringify(event));
  });
  const delay = ms => new Promise(resolve => setTimeout(resolve, ms));
  const beginning = Date.now();
  let finished = false, admission, waitingForOpening = false;
  try {
    while (Date.now() - beginning < 65_000) {
      if (error) throw error;
      if (child.exitCode !== null) throw new Error("Simulated caller exited before conversation completed");
      if (ready) {
        const chunks = await page.evaluate(() => window.__announcementEval.drainOutput());
        for (const chunk of chunks) child.stdin.write(JSON.stringify({ type: "audio", audio: chunk.data }) + "\n");
      }
      const snapshot = await state();
      const speaking = await page.evaluate(() => window.__announcementEval.speaking());
      if (snapshot.tasks.length && !admission) {
        admission = Date.now(); evidence.target_task_ids = [snapshot.tasks[0].id];
        await mark("adaptive.admission", { task_id: evidence.target_task_ids[0] });
      }
      // A fixed delay gives preference changes room to occur. No completion events
      // or grading information are delivered to the caller.
      if (admission && !finished && Date.now() - admission > 12_000) {
        await backend("action", { action: "finish", task_id: evidence.target_task_ids[0] });
        const snapshot = await state();
        evidence.eligible_ms[evidence.target_task_ids[0]] = snapshot.tasks[0].finished_at;
        waitingForOpening = speaking;
        finished = true;
      }
      if (finished && waitingForOpening && !speaking) {
        evidence.eligible_ms[evidence.target_task_ids[0]] = Date.now();
        await mark("adaptive.eligible"); waitingForOpening = false;
      }
      await delay(40);
    }
    if (!finished) throw new Error("Independent caller did not admit its intended task");
  } finally {
    if (child.exitCode === null) {
      child.stdin.write(JSON.stringify({ type: "close" }) + "\n");
      const deadline = Date.now() + 15_000;
      while (!closed && child.exitCode === null && Date.now() < deadline) await delay(50);
      child.stdin.end();
      if (child.exitCode === null && !closed) child.kill();
    }
    await pending;
    evidence.caller_finalized = closed;
    if (!closed) evidence.error = "Independent caller did not finalize";
    writeFileSync(join(root, "caller-relay.json"), JSON.stringify({ ready, closed, finished }));
  }
}
