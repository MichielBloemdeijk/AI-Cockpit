export type JarvisWakeStatus =
  | "idle"
  | "arming"
  | "armed"
  | "triggered"
  | "connecting"
  | "follow_up"
  | "fallback"
  | "destroyed";

export interface JarvisWakeSnapshot {
  status: JarvisWakeStatus;
  error: string | null;
  followUpUntil: number | null;
}

export interface JarvisWakeLifecycleDependencies {
  now: () => number;
  setTimer: (callback: () => void, milliseconds: number) => number;
  clearTimer: (timer: number) => void;
}

export interface JarvisWakeLifecycleOptions {
  followUpMilliseconds?: number;
  onSnapshot?: (snapshot: JarvisWakeSnapshot) => void;
  onRearmRequested?: (reason: "idle_timeout" | "explicit_sleep") => void;
}

const browserDependencies: JarvisWakeLifecycleDependencies = {
  now: () => Date.now(),
  setTimer: (callback, milliseconds) => window.setTimeout(callback, milliseconds),
  clearTimer: (timer) => window.clearTimeout(timer),
};

/** Deterministic lifecycle for wake-only and the wake-free follow-up window. */
export class JarvisWakeLifecycle {
  private readonly followUpMilliseconds: number;
  private readonly options: JarvisWakeLifecycleOptions;
  private readonly dependencies: JarvisWakeLifecycleDependencies;
  private followUpTimer: number | null = null;
  private followUpGeneration = 0;
  private snapshot: JarvisWakeSnapshot = {
    status: "idle",
    error: null,
    followUpUntil: null,
  };

  constructor(
    options: JarvisWakeLifecycleOptions = {},
    dependencies: JarvisWakeLifecycleDependencies = browserDependencies,
  ) {
    this.options = options;
    this.dependencies = dependencies;
    this.followUpMilliseconds = options.followUpMilliseconds ?? 60_000;
  }

  current(): JarvisWakeSnapshot {
    return this.snapshot;
  }

  beginArming(): void {
    this.assertActive();
    this.cancelFollowUp();
    this.publish({ status: "arming", error: null, followUpUntil: null });
  }

  armed(): void {
    this.requireStatus("arming");
    this.publish({ status: "armed", error: null, followUpUntil: null });
  }

  detected(): void {
    this.requireStatus("armed");
    this.publish({ status: "triggered", error: null, followUpUntil: null });
  }

  connecting(): void {
    this.requireStatus("triggered");
    this.publish({ status: "connecting", error: null, followUpUntil: null });
  }

  connected(): void {
    if (this.snapshot.status !== "connecting" && this.snapshot.status !== "follow_up") {
      throw new Error(`Cannot enter follow-up mode from ${this.snapshot.status}`);
    }
    this.scheduleFollowUp();
  }

  noteActivity(): void {
    this.assertActive();
    if (this.snapshot.status === "follow_up") this.scheduleFollowUp();
  }

  fail(cause: unknown): void {
    this.assertActive();
    this.cancelFollowUp();
    const error = cause instanceof Error ? cause.message : String(cause || "Wake detection failed");
    this.publish({ status: "fallback", error, followUpUntil: null });
  }

  sleep(): void {
    this.assertActive();
    this.cancelFollowUp();
    this.publish({ status: "arming", error: null, followUpUntil: null });
    this.options.onRearmRequested?.("explicit_sleep");
  }

  reset(): void {
    this.assertActive();
    this.cancelFollowUp();
    this.publish({ status: "idle", error: null, followUpUntil: null });
  }

  destroy(): void {
    if (this.snapshot.status === "destroyed") return;
    this.cancelFollowUp();
    this.publish({ status: "destroyed", error: null, followUpUntil: null });
  }

  private scheduleFollowUp(): void {
    this.cancelFollowUp();
    const generation = ++this.followUpGeneration;
    const followUpUntil = this.dependencies.now() + this.followUpMilliseconds;
    this.publish({ status: "follow_up", error: null, followUpUntil });
    this.followUpTimer = this.dependencies.setTimer(() => {
      this.followUpTimer = null;
      if (generation !== this.followUpGeneration || this.snapshot.status !== "follow_up") return;
      this.publish({ status: "arming", error: null, followUpUntil: null });
      this.options.onRearmRequested?.("idle_timeout");
    }, this.followUpMilliseconds);
  }

  private cancelFollowUp(): void {
    this.followUpGeneration += 1;
    if (this.followUpTimer === null) return;
    this.dependencies.clearTimer(this.followUpTimer);
    this.followUpTimer = null;
  }

  private requireStatus(status: JarvisWakeStatus): void {
    this.assertActive();
    if (this.snapshot.status !== status) {
      throw new Error(`Expected wake state ${status}, received ${this.snapshot.status}`);
    }
  }

  private assertActive(): void {
    if (this.snapshot.status === "destroyed") throw new Error("Wake lifecycle has been destroyed");
  }

  private publish(snapshot: JarvisWakeSnapshot): void {
    this.snapshot = snapshot;
    this.options.onSnapshot?.(snapshot);
  }
}
