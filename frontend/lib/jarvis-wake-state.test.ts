import { describe, expect, it, vi } from "vitest";

import {
  JarvisWakeLifecycle,
  type JarvisWakeLifecycleDependencies,
  type JarvisWakeSnapshot,
} from "./jarvis-wake-state";

function harness() {
  let now = 10_000;
  const timers: Array<() => void> = [];
  const snapshots: JarvisWakeSnapshot[] = [];
  const rearm = vi.fn();
  const dependencies: JarvisWakeLifecycleDependencies = {
    now: () => now,
    setTimer: (callback) => {
      timers.push(callback);
      return timers.length;
    },
    clearTimer: vi.fn(),
  };
  const lifecycle = new JarvisWakeLifecycle(
    {
      followUpMilliseconds: 60_000,
      onSnapshot: (snapshot) => snapshots.push(snapshot),
      onRearmRequested: rearm,
    },
    dependencies,
  );
  return { lifecycle, timers, snapshots, rearm, advance: (milliseconds: number) => { now += milliseconds; } };
}

describe("JarvisWakeLifecycle", () => {
  it("moves through arm, detection, connection, and the follow-up window", () => {
    const value = harness();
    value.lifecycle.beginArming();
    value.lifecycle.armed();
    value.lifecycle.detected();
    value.lifecycle.connecting();
    value.lifecycle.connected();

    expect(value.snapshots.map((snapshot) => snapshot.status)).toEqual([
      "arming",
      "armed",
      "triggered",
      "connecting",
      "follow_up",
    ]);
    expect(value.lifecycle.current().followUpUntil).toBe(70_000);
  });

  it("renews the 60-second window on activity and ignores the cancelled timer", () => {
    const value = harness();
    value.lifecycle.beginArming();
    value.lifecycle.armed();
    value.lifecycle.detected();
    value.lifecycle.connecting();
    value.lifecycle.connected();
    const firstTimer = value.timers[0];

    value.advance(20_000);
    value.lifecycle.noteActivity();
    firstTimer();

    expect(value.lifecycle.current()).toMatchObject({ status: "follow_up", followUpUntil: 90_000 });
    expect(value.rearm).not.toHaveBeenCalled();
    value.timers[1]();
    expect(value.lifecycle.current().status).toBe("arming");
    expect(value.rearm).toHaveBeenCalledWith("idle_timeout");
  });

  it("re-arms on explicit sleep and fails closed to manual fallback", () => {
    const value = harness();
    value.lifecycle.beginArming();
    value.lifecycle.armed();
    value.lifecycle.detected();
    value.lifecycle.connecting();
    value.lifecycle.connected();
    value.lifecycle.sleep();
    expect(value.rearm).toHaveBeenCalledWith("explicit_sleep");
    expect(value.lifecycle.current().status).toBe("arming");

    value.lifecycle.fail(new Error("model unavailable"));
    expect(value.lifecycle.current()).toEqual({
      status: "fallback",
      error: "model unavailable",
      followUpUntil: null,
    });
  });

  it("cancels timers and rejects transitions after destruction", () => {
    const value = harness();
    value.lifecycle.beginArming();
    value.lifecycle.armed();
    value.lifecycle.detected();
    value.lifecycle.connecting();
    value.lifecycle.connected();
    value.lifecycle.destroy();

    expect(value.lifecycle.current().status).toBe("destroyed");
    expect(() => value.lifecycle.beginArming()).toThrow("destroyed");
  });
});
