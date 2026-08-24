export type VoiceDeliveryState = "pending" | "queued" | "playing" | "spoken" | "dismissed";

export interface VoiceDeliveryRecord {
  eventId: string;
  deliveryId?: string;
  state: VoiceDeliveryState;
  updatedAt: number;
  text?: string;
  urgent?: boolean;
  answerRunId?: string;
}

const DB_NAME = "ai-cockpit-jarvis";
const STORE_NAME = "voice-deliveries";
const FALLBACK_KEY = "ai-cockpit-voice-deliveries";

function readFallback(): Record<string, VoiceDeliveryRecord> {
  try { return JSON.parse(localStorage.getItem(FALLBACK_KEY) || "{}"); } catch { return {}; }
}

function writeFallback(records: Record<string, VoiceDeliveryRecord>): void {
  try { localStorage.setItem(FALLBACK_KEY, JSON.stringify(records)); } catch { /* best effort */ }
}

function openDb(): Promise<IDBDatabase | null> {
  if (typeof indexedDB === "undefined") return Promise.resolve(null);
  return new Promise((resolve) => {
    const request = indexedDB.open(DB_NAME, 1);
    request.onupgradeneeded = () => request.result.createObjectStore(STORE_NAME, { keyPath: "eventId" });
    request.onsuccess = () => resolve(request.result);
    request.onerror = () => resolve(null);
  });
}

export async function getVoiceDelivery(eventId: string): Promise<VoiceDeliveryRecord | null> {
  const db = await openDb();
  if (!db) return readFallback()[eventId] || null;
  return new Promise((resolve) => {
    const request = db.transaction(STORE_NAME, "readonly").objectStore(STORE_NAME).get(eventId);
    request.onsuccess = () => resolve((request.result as VoiceDeliveryRecord | undefined) || null);
    request.onerror = () => resolve(null);
  });
}

export async function setVoiceDeliveryState(eventId: string, state: VoiceDeliveryState, deliveryId?: string, text?: string): Promise<void> {
  const db = await openDb();
  if (!db) {
    const records = readFallback();
    records[eventId] = { ...records[eventId], eventId, deliveryId: deliveryId || records[eventId]?.deliveryId, text: text ?? records[eventId]?.text, state, updatedAt: Date.now() };
    writeFallback(records);
    return;
  }
  await new Promise<void>((resolve) => {
    const transaction = db.transaction(STORE_NAME, "readwrite");
    const store = transaction.objectStore(STORE_NAME);
    const getRequest = store.get(eventId);
    getRequest.onsuccess = () => {
      const existing = getRequest.result as VoiceDeliveryRecord | undefined;
      store.put({ ...existing, eventId, deliveryId: deliveryId || existing?.deliveryId, text: text ?? existing?.text, state, updatedAt: Date.now() } satisfies VoiceDeliveryRecord);
    };
    transaction.oncomplete = () => resolve();
    transaction.onerror = () => resolve();
    transaction.onabort = () => resolve();
  });
}

export async function claimVoiceDelivery(eventId: string, deliveryId?: string, text?: string, urgent?: boolean, answerRunId?: string): Promise<boolean> {
  const db = await openDb();
  if (!db) {
    const records = readFallback();
    const existing = records[eventId];
    if (existing && existing.state !== "pending") {
      if (deliveryId && !existing.deliveryId) { existing.deliveryId = deliveryId; existing.updatedAt = Date.now(); writeFallback(records); }
      return false;
    }
    records[eventId] = { eventId, deliveryId, state: "queued", updatedAt: Date.now(), text, urgent, answerRunId };
    writeFallback(records);
    return true;
  }
  return new Promise((resolve) => {
    const transaction = db.transaction(STORE_NAME, "readwrite");
    const store = transaction.objectStore(STORE_NAME);
    const getRequest = store.get(eventId);
    let claimed = false;
    getRequest.onsuccess = () => {
      const existing = getRequest.result as VoiceDeliveryRecord | undefined;
      if (existing && existing.state !== "pending") {
        if (deliveryId && !existing.deliveryId) store.put({ ...existing, deliveryId, updatedAt: Date.now() } satisfies VoiceDeliveryRecord);
        return;
      }
      store.put({ eventId, deliveryId, state: "queued", updatedAt: Date.now(), text, urgent, answerRunId } satisfies VoiceDeliveryRecord);
      claimed = true;
    };
    transaction.oncomplete = () => resolve(claimed);
    transaction.onerror = () => resolve(false);
    transaction.onabort = () => resolve(false);
  });
}

export async function listRecoverableVoiceDeliveries(): Promise<VoiceDeliveryRecord[]> {
  const db = await openDb();
  const recoverable = (record: VoiceDeliveryRecord) => ["pending", "queued", "playing"].includes(record.state) && Boolean(record.text);
  if (!db) return Object.values(readFallback()).filter(recoverable);
  return new Promise((resolve) => {
    const request = db.transaction(STORE_NAME, "readonly").objectStore(STORE_NAME).getAll();
    request.onsuccess = () => resolve(((request.result || []) as VoiceDeliveryRecord[]).filter(recoverable));
    request.onerror = () => resolve([]);
  });
}
