import { afterEach, expect, it, vi } from "vitest";
import { watchResult } from "./result-live";

class FakeEventSource {
  static instances: FakeEventSource[] = [];
  listeners = new Map<string, (event: unknown) => void>();
  onopen?: () => void;
  onerror?: () => void;
  close = vi.fn();
  constructor(public url: string, public options: unknown) { FakeEventSource.instances.push(this); }
  addEventListener(name: string, callback: (event: unknown) => void) { this.listeners.set(name, callback); }
}

afterEach(() => { vi.useRealTimers(); vi.unstubAllGlobals(); FakeEventSource.instances = []; });

it("replays via cursor after bounded fallback and closes all timers", () => {
  vi.useFakeTimers(); vi.stubGlobal("EventSource", FakeEventSource);
  const callbacks = { snapshot: vi.fn(), reconcile: vi.fn(), connection: vi.fn(), observe: vi.fn() };
  const stop = watchResult("opaque", callbacks);
  const first = FakeEventSource.instances[0];
  expect(first.options).toEqual({ withCredentials: true });
  first.onopen?.();
  expect(callbacks.observe).toHaveBeenLastCalledWith("sse_connect");
  first.listeners.get("snapshot")?.({ data: JSON.stringify({ public_id: "opaque", state_revision: 8 }), lastEventId: "5" });
  first.listeners.get("result")?.({ lastEventId: "8" });
  expect(callbacks.snapshot).toHaveBeenCalledWith({ public_id: "opaque", state_revision: 8 });
  expect(callbacks.reconcile).toHaveBeenCalledOnce();
  first.onerror?.(); first.onerror?.(); first.onerror?.();
  expect(callbacks.observe).toHaveBeenLastCalledWith("sse_fallback");
  expect(callbacks.connection).toHaveBeenLastCalledWith("polling");
  vi.advanceTimersByTime(15000);
  expect(FakeEventSource.instances[1].url).toContain("cursor=8");
  expect(first.close).toHaveBeenCalledOnce();
  FakeEventSource.instances[1].onopen?.();
  expect(callbacks.observe).toHaveBeenLastCalledWith("sse_reconnect");
  stop();
  const calls = callbacks.reconcile.mock.calls.length;
  vi.advanceTimersByTime(120000);
  expect(callbacks.reconcile).toHaveBeenCalledTimes(calls);
  expect(FakeEventSource.instances[1].close).toHaveBeenCalledOnce();
});

it("uses periodic reconciliation when SSE is unavailable", () => {
  vi.useFakeTimers(); vi.stubGlobal("EventSource", undefined);
  const callbacks = { snapshot: vi.fn(), reconcile: vi.fn(), connection: vi.fn() };
  const stop = watchResult("opaque", callbacks);
  expect(callbacks.connection).toHaveBeenCalledWith("polling");
  vi.advanceTimersByTime(10000);
  expect(callbacks.reconcile).toHaveBeenCalledTimes(2);
  stop();
});

it("closes offline transport and resumes with its cursor without accepting stale callbacks", () => {
  vi.useFakeTimers(); vi.stubGlobal("EventSource", FakeEventSource);
  const surface = new EventTarget();
  vi.stubGlobal("window", surface);
  const callbacks = { snapshot: vi.fn(), reconcile: vi.fn(), connection: vi.fn() };
  const stop = watchResult("opaque", callbacks);
  const first = FakeEventSource.instances[0];
  first.listeners.get("snapshot")?.({ data: '{"public_id":"opaque"}', lastEventId: "12" });
  surface.dispatchEvent(new Event("offline"));
  expect(first.close).toHaveBeenCalledOnce();
  expect(callbacks.connection).toHaveBeenLastCalledWith("reconnecting");
  vi.advanceTimersByTime(60000);
  expect(callbacks.reconcile).not.toHaveBeenCalled();
  surface.dispatchEvent(new Event("online"));
  expect(callbacks.reconcile).toHaveBeenCalledOnce();
  const resumed = FakeEventSource.instances[1];
  expect(resumed.url).toContain("cursor=12");
  first.onerror?.(); first.onopen?.();
  expect(resumed.close).not.toHaveBeenCalled();
  expect(callbacks.connection).toHaveBeenLastCalledWith("reconnecting");
  resumed.onopen?.();
  expect(callbacks.connection).toHaveBeenLastCalledWith("live");
  stop();
  surface.dispatchEvent(new Event("offline")); surface.dispatchEvent(new Event("online"));
  expect(FakeEventSource.instances).toHaveLength(2);
});
