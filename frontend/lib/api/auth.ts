/** Owner and device sessions. */
import { ApiError, apiRequest } from "./http";

export interface AccessSession {
  authenticated: boolean;
  scope: "owner" | "device";
  device: { id: string; label: string; paired: boolean } | null;
}

const AUTH_CHECK_TIMEOUT_MS = 5000;

export async function login(password: string): Promise<void> {
  try {
    await apiRequest("/api/auth/login", { method: "POST", body: { password } });
  } catch (error) {
    if (error instanceof ApiError && error.status === 401) throw new ApiError("http", "Invalid password.", { status: 401, detail: error.detail });
    throw error;
  }
}

/** Leaving never waits on the server: a failed logout still ends the local session. */
export async function logout(): Promise<void> {
  try {
    await apiRequest("/api/auth/logout", { method: "POST" });
  } catch {
    // The cookie expires on its own; the caller navigates away either way.
  }
}

export async function checkAuth(): Promise<boolean> {
  try {
    await apiRequest("/api/auth/status", { timeoutMs: AUTH_CHECK_TIMEOUT_MS });
    return true;
  } catch {
    return false;
  }
}

export function getAccessSession(): Promise<AccessSession> {
  return apiRequest("/api/auth/status", { timeoutMs: AUTH_CHECK_TIMEOUT_MS });
}

export function getAccessConfig(): Promise<{ password_required: boolean }> {
  return apiRequest("/api/auth/config", { timeoutMs: AUTH_CHECK_TIMEOUT_MS });
}
