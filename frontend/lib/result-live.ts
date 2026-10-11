import { resultEventsUrl, type MaintainedResult } from "./api";

/** Durable SSE IDs survive native reconnect and explicit fallback reconnects. */
export function watchResult(publicId: string, callbacks: {
  snapshot: (value: MaintainedResult) => void;
  reconcile: () => void;
  connection: (state: "live" | "reconnecting" | "polling") => void;
  observe?: (kind: "sse_connect" | "sse_reconnect" | "sse_fallback") => void;
}): () => void {
  let source: EventSource | null = null;
  let cursor: string | undefined;
  let failures = 0;
  let opened = false;
  let disposed = false;
  let offline = typeof navigator !== "undefined" && navigator.onLine === false;
  let fallback: ReturnType<typeof setInterval> | undefined;
  let retry: ReturnType<typeof setTimeout> | undefined;
  const reconcile = setInterval(() => { if (!offline) callbacks.reconcile(); }, 30000);
  const polling = () => {
    callbacks.connection("polling");
    if (!fallback) { callbacks.observe?.("sse_fallback"); fallback = setInterval(callbacks.reconcile, 5000); }
  };
  const connect = () => {
    if (disposed || offline) return;
    if (typeof EventSource === "undefined") { polling(); return; }
    source = new EventSource(resultEventsUrl(publicId, cursor), { withCredentials: true });
    const current = source;
    source.onopen = () => {
      if (disposed || source !== current) return;
      failures = 0;
      if (fallback) { clearInterval(fallback); fallback = undefined; }
      callbacks.connection("live");
      callbacks.observe?.(opened ? "sse_reconnect" : "sse_connect");
      opened = true;
    };
    source.addEventListener("snapshot", (event) => {
      if (disposed || source !== current) return;
      const message = event as MessageEvent<string>;
      try {
        const snapshot = JSON.parse(message.data) as MaintainedResult;
        callbacks.snapshot(snapshot);
        cursor = message.lastEventId || cursor;
      } catch { callbacks.reconcile(); }
    });
    source.addEventListener("result", (event) => {
      if (disposed || source !== current) return;
      cursor = (event as MessageEvent).lastEventId || cursor;
      // Only authenticated snapshots choose renderers/content/actions. Replayed
      // old state events cannot regress a newer snapshot already on screen.
      callbacks.reconcile();
    });
    source.onerror = () => {
      if (disposed || source !== current) return;
      callbacks.connection("reconnecting");
      if (++failures < 3) return;
      source?.close();
      polling();
      retry = setTimeout(connect, 15000);
    };
  };
  const wentOffline = () => {
    offline = true;
    source?.close(); source = null;
    if (retry) { clearTimeout(retry); retry = undefined; }
    if (fallback) { clearInterval(fallback); fallback = undefined; }
    callbacks.connection("reconnecting");
  };
  const wentOnline = () => {
    if (disposed || !offline) return;
    offline = false; failures = 0;
    callbacks.reconcile();
    connect();
  };
  if (typeof window !== "undefined") {
    window.addEventListener("offline", wentOffline);
    window.addEventListener("online", wentOnline);
  }
  if (offline) callbacks.connection("reconnecting"); else connect();
  return () => {
    disposed = true;
    if (typeof window !== "undefined") {
      window.removeEventListener("offline", wentOffline);
      window.removeEventListener("online", wentOnline);
    }
    source?.close();
    clearInterval(reconcile);
    if (fallback) clearInterval(fallback);
    if (retry) clearTimeout(retry);
  };
}
