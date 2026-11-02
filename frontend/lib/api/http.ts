/**
 * The one transport for finite backend requests.
 *
 * Every request has a deadline that covers the response body too, so a stalled
 * request always settles and later polling recovers. A caller's own abort stays
 * distinguishable from that deadline, and every failure is an `ApiError` with the
 * HTTP status and the backend's `detail` intact. Long-lived streams (chat SSE,
 * the Jarvis and conversation event streams) do not use this transport.
 */

export function resolveApiBase(): string {
  return process.env.NEXT_PUBLIC_API_URL?.trim().replace(/\/$/, "") ?? "";
}

export const API_BASE = resolveApiBase();

/** Ordinary reads and commands. */
export const DEFAULT_TIMEOUT_MS = 15_000;
/** Requests that wait for a model reply (council, synchronous chat turns). */
export const MODEL_REPLY_TIMEOUT_MS = 180_000;

export type ApiErrorKind = "http" | "timeout" | "aborted" | "network";

export class ApiError extends Error {
  readonly kind: ApiErrorKind;
  /** HTTP status for `http` errors, otherwise null. */
  readonly status: number | null;
  /** The backend's `detail` (a string or a validation structure), when it sent one. */
  readonly detail: unknown;

  constructor(kind: ApiErrorKind, message: string, options: { status?: number | null; detail?: unknown } = {}) {
    super(message);
    this.name = "ApiError";
    this.kind = kind;
    this.status = options.status ?? null;
    this.detail = options.detail ?? null;
  }
}

export interface RequestOptions {
  method?: "GET" | "POST" | "PUT" | "PATCH" | "DELETE";
  /** Sent as JSON. */
  body?: unknown;
  signal?: AbortSignal;
  timeoutMs?: number;
  /** Let the request outlive the page (telemetry, session close). */
  keepalive?: boolean;
}

const TIMEOUT_MESSAGE = "Request timed out. Please try again.";
const NETWORK_MESSAGE = "Unable to reach the backend. Check that the app is running.";

function httpError(status: number, body: string): ApiError {
  let detail: unknown = null;
  try {
    detail = (JSON.parse(body) as { detail?: unknown }).detail ?? null;
  } catch {
    detail = body.trim() || null;
  }
  const message = typeof detail === "string" && detail ? detail : `Request failed (HTTP ${status}).`;
  return new ApiError("http", message, { status, detail });
}

/** Parse a JSON response (undefined for an empty body), or throw its `ApiError`. */
export async function readJsonResponse<T>(response: Response): Promise<T> {
  const text = await response.text();
  if (!response.ok) throw httpError(response.status, text);
  return (text ? JSON.parse(text) : undefined) as T;
}

/** Send a request and parse its JSON response (undefined for an empty body). */
export async function apiRequest<T>(path: string, options: RequestOptions = {}): Promise<T> {
  const { method = "GET", body, signal, timeoutMs = DEFAULT_TIMEOUT_MS, keepalive } = options;
  const controller = new AbortController();
  const forwardAbort = () => controller.abort(signal?.reason);
  if (signal?.aborted) forwardAbort();
  else signal?.addEventListener("abort", forwardAbort, { once: true });
  let timedOut = false;
  const timer = setTimeout(() => {
    timedOut = true;
    controller.abort(new Error(TIMEOUT_MESSAGE));
  }, timeoutMs);
  try {
    let response: Response;
    try {
      response = await fetch(`${API_BASE}${path}`, {
        method,
        credentials: "include",
        cache: "no-store",
        keepalive,
        signal: controller.signal,
        ...(body === undefined ? {} : { headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) }),
      });
    } catch {
      throw new ApiError("network", NETWORK_MESSAGE);
    }
    return await readJsonResponse<T>(response);
  } catch (error) {
    if (timedOut) throw new ApiError("timeout", TIMEOUT_MESSAGE);
    if (signal?.aborted) throw new ApiError("aborted", "Request cancelled.");
    throw error;
  } finally {
    clearTimeout(timer);
    signal?.removeEventListener("abort", forwardAbort);
  }
}
