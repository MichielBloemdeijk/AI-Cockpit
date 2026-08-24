/* Root-scoped Jarvis notification worker. */
self.addEventListener("install", () => self.skipWaiting());
self.addEventListener("activate", (event) => event.waitUntil(self.clients.claim()));

self.addEventListener("push", (event) => {
  const payload = event.data ? event.data.json() : {};
  event.waitUntil((async () => {
    const clients = await self.clients.matchAll({ type: "window", includeUncontrolled: true });
    const visible = clients.find((client) => client.visibilityState === "visible" && new URL(client.url).pathname === "/voice");
    if (visible) {
      visible.postMessage({ type: "jarvis-push", payload });
      return;
    }
    await self.registration.showNotification(payload.task_title || "Jarvis update", {
      body: payload.summary || "There is a new Jarvis update.",
      tag: `jarvis-${payload.event_id || payload.delivery_id || Date.now()}`,
      data: payload,
      renotify: payload.importance === "urgent",
      badge: "/icon-192.svg",
      icon: "/icon-192.svg",
    });
  })());
});

self.addEventListener("notificationclick", (event) => {
  event.notification.close();
  const payload = event.notification.data || {};
  const url = new URL(payload.url || "/voice", self.location.origin);
  if (payload.delivery_id) url.searchParams.set("delivery", payload.delivery_id);
  event.waitUntil((async () => {
    const clients = await self.clients.matchAll({ type: "window", includeUncontrolled: true });
    const existing = clients.find((client) => new URL(client.url).pathname === "/voice" && "focus" in client);
    if (existing) {
      await existing.focus();
      const target = new URL("/voice", self.location.origin);
      if (payload.event_id) target.searchParams.set("update", payload.event_id);
      if (payload.delivery_id) target.searchParams.set("delivery", payload.delivery_id);
      if ("navigate" in existing) await existing.navigate(target.toString());
      existing.postMessage({ type: "jarvis-open-update", payload });
      return;
    }
    await self.clients.openWindow(url.toString());
  })());
});
