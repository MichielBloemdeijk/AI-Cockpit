/** Web Push subscriptions and delivery receipts. */
import { apiRequest } from "./http";

export function getVapidPublicKey(): Promise<{ enabled: boolean; public_key: string }> {
  return apiRequest("/api/notifications/vapid-public-key");
}

export function savePushSubscription(subscription: PushSubscription): Promise<{ id: string; endpoint: string }> {
  const json = subscription.toJSON();
  return apiRequest("/api/notifications/subscriptions", {
    method: "POST",
    body: { endpoint: json.endpoint, keys: json.keys, user_agent: navigator.userAgent },
  });
}

/** Best effort: an unrecorded receipt never blocks opening the result. */
export async function acknowledgePushDelivery(deliveryId: string, state: "opened" = "opened"): Promise<void> {
  try {
    await apiRequest(`/api/notifications/deliveries/${encodeURIComponent(deliveryId)}/ack`, { method: "POST", body: { state } });
  } catch {
    // The receipt is diagnostic; the result opens regardless.
  }
}
