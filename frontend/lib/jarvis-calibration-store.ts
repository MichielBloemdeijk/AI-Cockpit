import { createUuid } from "./uuid";

export type CalibrationCategory = "normal" | "quiet" | "distant" | "noisy" | "hard_negative";

export interface CalibrationSample {
  id: string;
  category: CalibrationCategory;
  createdAt: string;
  mimeType: string;
  audio: Blob;
}

const DB_NAME = "jarvis-local-calibration";
const STORE = "samples";

function openDatabase(): Promise<IDBDatabase> {
  return new Promise((resolve, reject) => {
    const request = indexedDB.open(DB_NAME, 1);
    request.onupgradeneeded = () => {
      if (!request.result.objectStoreNames.contains(STORE)) {
        request.result.createObjectStore(STORE, { keyPath: "id" });
      }
    };
    request.onsuccess = () => resolve(request.result);
    request.onerror = () => reject(request.error ?? new Error("Unable to open calibration storage"));
  });
}

async function transaction<T>(mode: IDBTransactionMode, action: (store: IDBObjectStore) => IDBRequest<T>): Promise<T> {
  const db = await openDatabase();
  try {
    return await new Promise<T>((resolve, reject) => {
      const request = action(db.transaction(STORE, mode).objectStore(STORE));
      request.onsuccess = () => resolve(request.result);
      request.onerror = () => reject(request.error ?? new Error("Calibration storage failed"));
    });
  } finally {
    db.close();
  }
}

export async function saveCalibrationSample(category: CalibrationCategory, audio: Blob): Promise<CalibrationSample> {
  const sample: CalibrationSample = {
    id: createUuid(), category, createdAt: new Date().toISOString(),
    mimeType: audio.type || "audio/webm", audio,
  };
  await transaction("readwrite", (store) => store.put(sample));
  return sample;
}

export async function listCalibrationSamples(): Promise<CalibrationSample[]> {
  const values = await transaction<CalibrationSample[]>("readonly", (store) => store.getAll());
  return values.sort((a, b) => a.createdAt.localeCompare(b.createdAt));
}

export async function deleteCalibrationSample(id: string): Promise<void> {
  await transaction("readwrite", (store) => store.delete(id));
}

export async function clearCalibrationSamples(): Promise<void> {
  await transaction("readwrite", (store) => store.clear());
}
