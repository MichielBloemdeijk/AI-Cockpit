import { postResultObservation } from "./api";
import { createUuid } from "./uuid";

type Milestone = "first_snapshot" | "first_ready" | "sse_connect" | "sse_reconnect" | "sse_fallback";

/** Metadata only; bounded, best-effort, no retries and never on the UI path. */
export function resultViewTelemetry(publicId: string) {
  const started = performance.now();
  let viewId: string;
  try { viewId = createUuid(); } catch { return (_kind: Milestone, _revision = 0) => {}; }
  let sequence = 0;
  const first = new Set<string>();
  return (kind: Milestone, revision = 0) => {
    if (sequence >= 1000 || (kind.startsWith("first_") && first.has(kind))) return;
    first.add(kind);
    try {
      void postResultObservation(publicId, { view_id: viewId, sequence: sequence++, kind,
        duration_ms: Math.min(3600000, Math.max(0, performance.now() - started)), revision_number: revision }).catch(() => {});
    } catch { /* Missing transport/test stub must not fail rendering. */ }
  };
}
