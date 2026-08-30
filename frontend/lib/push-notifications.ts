import { getVapidPublicKey, savePushSubscription } from "./api";

export type PushNotificationState =
  | "checking"
  | "unavailable"
  | "not_configured"
  | "blocked"
  | "available"
  | "enabled"
  | "error";

export function decodeVapidPublicKey(value: string): Uint8Array<ArrayBuffer> {
  const padding = "=".repeat((4 - (value.length % 4)) % 4);
  const decoded = atob((value + padding).replaceAll("-", "+").replaceAll("_", "/"));
  const bytes = new Uint8Array(decoded.length);
  for (let index = 0; index < decoded.length; index += 1) {
    bytes[index] = decoded.charCodeAt(index);
  }
  return bytes;
}

function browserSupportsPush(): boolean {
  return typeof window !== "undefined"
    && "serviceWorker" in navigator
    && "PushManager" in window
    && "Notification" in window;
}

export async function inspectPushNotifications(): Promise<PushNotificationState> {
  if (!browserSupportsPush()) return "unavailable";
  const config = await getVapidPublicKey();
  if (!config.enabled || !config.public_key) return "not_configured";
  if (Notification.permission === "denied") return "blocked";
  const registration = await navigator.serviceWorker.register("/sw.js", {
    scope: "/",
    updateViaCache: "none",
  });
  const subscription = await registration.pushManager.getSubscription();
  if (!subscription) return "available";
  await savePushSubscription(subscription);
  return "enabled";
}

export async function enablePushNotifications(): Promise<PushNotificationState> {
  if (!browserSupportsPush()) return "unavailable";
  const config = await getVapidPublicKey();
  if (!config.enabled || !config.public_key) return "not_configured";
  const permission = await Notification.requestPermission();
  if (permission !== "granted") return "blocked";
  const registration = await navigator.serviceWorker.register("/sw.js", {
    scope: "/",
    updateViaCache: "none",
  });
  const existing = await registration.pushManager.getSubscription();
  const subscription = existing ?? await registration.pushManager.subscribe({
    userVisibleOnly: true,
    applicationServerKey: decodeVapidPublicKey(config.public_key),
  });
  await savePushSubscription(subscription);
  return "enabled";
}
