import { readFileSync } from "node:fs";
import { describe, expect, it, vi } from "vitest";

type WorkerListener = (event: Record<string, unknown>) => void;

function loadWorker(windowClients: Array<Record<string, unknown>> = []) {
  const listeners: Record<string, WorkerListener> = {};
  const showNotification = vi.fn(async () => undefined);
  const openWindow = vi.fn(async () => undefined);
  const clientsApi = {
    claim: vi.fn(async () => undefined),
    matchAll: vi.fn(async () => windowClients),
    openWindow,
  };
  const worker = {
    location: { origin: "https://jarvis.test" },
    skipWaiting: vi.fn(),
    clients: clientsApi,
    registration: { showNotification },
    addEventListener: (type: string, listener: WorkerListener) => { listeners[type] = listener; },
  };
  const source = readFileSync(new URL("../public/sw.js", import.meta.url), "utf8");
  new Function("self", "clients", source)(worker, clientsApi);
  return { listeners, showNotification, openWindow };
}

describe("Jarvis service worker", () => {
  it("delivers a push to a visible Jarvis page without also showing a notification", async () => {
    const postMessage = vi.fn();
    const { listeners, showNotification } = loadWorker([{
      visibilityState: "visible",
      url: "https://jarvis.test/jarvis",
      postMessage,
    }]);
    let work: Promise<unknown> = Promise.resolve();
    listeners.push({
      data: { json: () => ({ event_id: "event-1", delivery_id: "delivery-1" }) },
      waitUntil: (promise: Promise<unknown>) => { work = promise; },
    });
    await work;
    expect(postMessage).toHaveBeenCalledWith({
      type: "jarvis-push",
      payload: { event_id: "event-1", delivery_id: "delivery-1" },
    });
    expect(showNotification).not.toHaveBeenCalled();
  });

  it("opens the durable event and delivery deep link when a notification is clicked", async () => {
    const { listeners, openWindow } = loadWorker();
    let work: Promise<unknown> = Promise.resolve();
    listeners.notificationclick({
      notification: {
        close: vi.fn(),
        data: { event_id: "event-2", delivery_id: "delivery-2", url: "/jarvis?update=event-2" },
      },
      waitUntil: (promise: Promise<unknown>) => { work = promise; },
    });
    await work;
    expect(openWindow).toHaveBeenCalledWith("https://jarvis.test/jarvis?update=event-2&delivery=delivery-2");
  });
});
