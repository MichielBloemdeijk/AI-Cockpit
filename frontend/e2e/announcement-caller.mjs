/** Independent duplex caller. Only agenda and microphone audio cross its boundary. */
import { spawn } from "node:child_process";
import { createInterface } from "node:readline";
import { appendFileSync, writeFileSync } from "node:fs";
import { join } from "node:path";

export async function runCaller({ page, spec, state, backend, mark, evidence }) {
  const root = process.env.JARVIS_EVAL_ROOT;
  const child = spawn(spec.python, ["-m", "app.selftest.caller"], {
    cwd: spec.backend_directory, windowsHide: true,
    env: { ...process.env, JARVIS_CALLER_AGENDA: JSON.stringify(spec.adaptive) },
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
  // Scenario-controlled completion schedule; the caller never learns about it.
  const schedule = spec.adaptive.tasks || [{ finish_after_seconds: 12 }];
  const admitted = new Map(), done = new Set(), retried = new Set();
  let finished = false, waitingForOpening = false;
  try {
    while (Date.now() - beginning < (spec.adaptive.max_seconds || 90) * 1000) {
      if (error) throw error;
      if (child.exitCode !== null) throw new Error("Simulated caller exited before conversation completed");
      if (ready) {
        const chunks = await page.evaluate(() => window.__announcementEval.drainOutput());
        for (const chunk of chunks) child.stdin.write(JSON.stringify({ type: "audio", audio: chunk.data }) + "\n");
      }
      const snapshot = await state();
      const speaking = await page.evaluate(() => window.__announcementEval.speaking());
      // Unscheduled tasks (e.g. background retries) finish after a default delay.
      for (const task of snapshot.tasks) {
        if (done.has(task.id) && ["queued", "running", "waiting_for_worker"].includes(task.status)) {
          done.delete(task.id); admitted.set(task.id, Date.now()); retried.add(task.id);  // retried after finishing
        }
        if (!admitted.has(task.id)) {
          admitted.set(task.id, Date.now()); evidence.target_task_ids.push(task.id);
          await mark("adaptive.admission", { task_id: task.id });
        }
      }
      // No completion events or grading information are delivered to the caller.
      for (const [index, taskId] of evidence.target_task_ids.entries()) {
        const plan = (!retried.has(taskId) && schedule[index]) || { finish_after_seconds: 12 };
        if (done.has(taskId) || Date.now() - admitted.get(taskId) < (plan.finish_after_seconds ?? 12) * 1000) continue;
        await backend("action", { action: "finish", task_id: taskId, status: plan.status || "completed", summary: plan.summary });
        const after = await state();
        evidence.eligible_ms[taskId] = after.tasks.find(t => t.id === taskId).finished_at;
        await mark("completion", { task_id: taskId });
        done.add(taskId); waitingForOpening = speaking; finished = true;
      }
      if (finished && waitingForOpening && !speaking) {
        await mark("adaptive.eligible"); waitingForOpening = false;
      }
      await delay(40);
    }
    if (schedule.length && !finished) throw new Error("Simulated user did not get its intended task admitted");
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
