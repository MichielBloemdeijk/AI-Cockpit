// The focused FastAPI export aliases future IDs to fixed, data-free shells.
// Load HTML there, not unbuilt Next RSC routes.
export const staticShell = process.env.NEXT_PUBLIC_COCKPIT_STATIC_SHELL === "1";
export const defaultLoginPath = staticShell ? "/workspace/apps" : "/chat";

export function safeNextPath(candidate: string | null, fallback = defaultLoginPath): string {
  if (!candidate || !candidate.startsWith("/") || candidate.startsWith("//") || /[\\\x00-\x20\x7f]/.test(candidate)) return fallback;
  try {
    const target = new URL(candidate, "https://cockpit.invalid");
    return target.origin === "https://cockpit.invalid" ? candidate : fallback;
  } catch { return fallback; }
}

export function loginReturnUrl(): string {
  const next = window.location.pathname + window.location.search + window.location.hash;
  return `/login?next=${encodeURIComponent(next)}`;
}
