import { expect, test, type Page } from "@playwright/test";

import { loadJarvisLiveSmokeFixture } from "./jarvis-live-fixture.mjs";

type SmokeState = {
  current_log: {
    id: string;
    messages: Array<{
      id: string;
      role: "user" | "assistant" | "system";
      content: string;
      modality?: string;
    }>;
  } | null;
  live_event_cursor: number;
  session: {
    id: string;
    generation: number;
    provider_adapter: string;
    lifecycle_state: string;
  } | null;
};

type SmokeObservation = {
  state: SmokeState | null;
  diagnostics: string[];
  errors: string[];
  audio: { currentTime: number; paused: boolean; hasStream: boolean };
};

const fixture = loadJarvisLiveSmokeFixture();

async function observe(page: Page): Promise<SmokeObservation> {
  return page.evaluate(async () => {
    const bridge = window.__jarvisLiveSmoke;
    const response = await fetch("/api/jarvis/state", { credentials: "include", cache: "no-store" });
    const state = response.ok ? await response.json() as SmokeState : null;
    const audio = document.querySelector("audio");
    const errors = [...document.querySelectorAll(".text-rose-200, .text-red-400")]
      .map((element) => element.textContent?.trim() || "")
      .filter(Boolean);
    return {
      state,
      diagnostics: bridge?.diagnostics ?? [],
      errors,
      audio: {
        currentTime: audio?.currentTime ?? 0,
        paused: audio?.paused ?? true,
        hasStream: Boolean(audio?.srcObject),
      },
    };
  });
}

async function waitFor(
  page: Page,
  description: string,
  predicate: (observation: SmokeObservation) => boolean,
  timeoutMs: number,
): Promise<SmokeObservation> {
  const deadline = Date.now() + timeoutMs;
  let last: SmokeObservation | null = null;
  while (Date.now() < deadline) {
    last = await observe(page);
    if (last.errors.length) {
      throw new Error(`${description} failed in the browser: ${last.errors.join(" | ")}; diagnostics=${last.diagnostics.join(",")}`);
    }
    if (predicate(last)) return last;
    await page.waitForTimeout(250);
  }
  throw new Error(
    `Timed out waiting for ${description}; session=${JSON.stringify(last?.state?.session ?? null)}; `
    + `audio=${JSON.stringify(last?.audio ?? null)}; diagnostics=${last?.diagnostics.join(",") || "none"}`,
  );
}

test("a recorded Jarvis wake opens GPT-Live and receives a spoken response", async ({ context, page }) => {
  const browserErrors: string[] = [];
  const failedApiResponses: string[] = [];
  page.on("pageerror", (error) => browserErrors.push(error.message));
  page.on("console", (message) => {
    if (
      message.type() === "error"
      && !message.text().startsWith("Failed to load resource: the server responded with a status of")
    ) browserErrors.push(message.text());
  });
  page.on("response", (response) => {
    if (response.status() >= 400 && response.url().includes("/api/")) {
      failedApiResponses.push(`${response.status()} ${response.url()}`);
    }
  });

  await context.addInitScript(({ base64, mimeType }) => {
    type Bridge = {
      diagnostics: string[];
      releaseAudio: () => Promise<void>;
    };
    const bridge: Bridge = {
      diagnostics: [],
      releaseAudio: async () => {
        throw new Error("The Jarvis microphone has not been requested yet");
      },
    };
    Object.defineProperty(window, "__jarvisLiveSmoke", { configurable: true, value: bridge });
    try {
    } catch {
      // about:blank has an opaque origin; the same script runs again on the app origin.
    }

    const nativeFetch = window.fetch.bind(window);
    window.fetch = (async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = typeof input === "string" ? input : input instanceof URL ? input.href : input.url;
      if (url.includes("/api/jarvis/live/telemetry") && typeof init?.body === "string") {
        try {
          const payload = JSON.parse(init.body) as { events?: Array<{ event_type?: string }> };
          for (const event of payload.events ?? []) {
            if (event.event_type) bridge.diagnostics.push(event.event_type);
          }
        } catch {
          // Malformed diagnostics must not affect the application request.
        }
      }
      return nativeFetch(input, init);
    }) as typeof window.fetch;

    const nativeGetUserMedia = navigator.mediaDevices.getUserMedia.bind(navigator.mediaDevices);
    Object.defineProperty(navigator.mediaDevices, "getUserMedia", {
      configurable: true,
      value: async (constraints?: MediaStreamConstraints) => {
        if (!constraints?.audio || constraints.video) return nativeGetUserMedia(constraints);
        // React's development lifecycle may stop the first microphone stream
        // and request another. Each request needs a new live track; replaying
        // a cached, stopped stream leaves wake inference listening to silence.
        return (async () => {
          const context = new AudioContext();
          const destination = context.createMediaStreamDestination();
          const audio = new Audio(`data:${mimeType};base64,${base64}`);
          audio.preload = "auto";
          const source = context.createMediaElementSource(audio);
          source.connect(destination);
          await context.resume();
          await new Promise<void>((resolve, reject) => {
            if (audio.readyState >= HTMLMediaElement.HAVE_FUTURE_DATA) {
              resolve();
              return;
            }
            audio.addEventListener("canplay", () => resolve(), { once: true });
            audio.addEventListener("error", () => reject(new Error("The wake recording could not be decoded")), { once: true });
          });
          bridge.releaseAudio = async () => {
            await context.resume();
            audio.currentTime = 0;
            await audio.play();
          };
          return destination.stream;
        })();
      },
    });
  }, { base64: fixture.base64, mimeType: fixture.mimeType });

  const login = await page.request.post("/api/auth/login", {
    data: { password: process.env.JARVIS_SMOKE_PASSWORD ?? "" },
  });
  if (login.status() === 401) {
    throw new Error("Local authentication is enabled; set JARVIS_SMOKE_PASSWORD before running the Live smoke test");
  }
  if (!login.ok()) throw new Error(`Jarvis smoke login failed (HTTP ${login.status()}): ${await login.text()}`);

  const freshLog = await page.request.post("/api/jarvis/logs/new", {
    data: { idempotency_key: `live-smoke-${crypto.randomUUID()}` },
  });
  if (!freshLog.ok()) {
    throw new Error(`Unable to create the isolated Jarvis log (HTTP ${freshLog.status()}): ${await freshLog.text()}`);
  }
  const freshPayload = await freshLog.json() as { current_log: { id: string } };
  const conversationId = freshPayload.current_log.id;

  let acceptedSession: { id: string; generation: number } | null = null;
  try {
    await page.goto("/jarvis", { waitUntil: "domcontentloaded" });
    await expect(page.getByText("GPT-Live", { exact: true }).first()).toBeVisible();
    if (await page.getByText("wake model pending", { exact: true }).isVisible()) {
      throw new Error("The browser wake model is not configured; set NEXT_PUBLIC_JARVIS_WAKE_MODEL_URL and restart the frontend");
    }
    await expect(page.getByText("wake armed", { exact: true })).toBeVisible({ timeout: 30_000 });
    await page.evaluate(async () => window.__jarvisLiveSmoke.releaseAudio());

    const connected = await waitFor(
      page,
      "combined GPT-Live readiness",
      (entry) => entry.state?.session?.provider_adapter === "openai_live"
        && entry.state?.session?.lifecycle_state === "ready"
        && entry.diagnostics.includes("live.sdp_applied")
        && entry.diagnostics.includes("live.data_channel_open")
        && entry.diagnostics.includes("live.backend_ready")
        && entry.diagnostics.includes("live.activation_audio_released"),
      45_000,
    );
    acceptedSession = connected.state?.session
      ? { id: connected.state.session.id, generation: connected.state.session.generation }
      : null;

    const eventCursor = connected.state?.live_event_cursor ?? 0;
    const response = await waitFor(
      page,
      "a completed Jarvis voice response",
      (entry) => {
        const messages = entry.state?.current_log?.id === conversationId
          ? entry.state.current_log.messages
          : [];
        return messages.some((message) => message.role === "user" && message.content.trim().length > 0)
          && messages.some((message) => message.role === "assistant" && message.content.trim().length > 0)
          && entry.audio.hasStream
          && entry.audio.currentTime > 0.05;
      },
      45_000,
    );
    expect(response.state?.live_event_cursor).toBeGreaterThan(eventCursor);
    expect(browserErrors, `Browser errors: ${browserErrors.join(" | ")}`).toEqual([]);
    expect(failedApiResponses, `Failed API responses: ${failedApiResponses.join(" | ")}`).toEqual([]);

    await page.getByRole("button", { name: "Sleep", exact: true }).click();
    await waitFor(
      page,
      "the exact Live session to close",
      (entry) => entry.state?.session?.id !== acceptedSession?.id
        || entry.state?.session?.lifecycle_state === "disconnected",
      10_000,
    );
  } finally {
    if (acceptedSession) {
      const state = await page.request.get("/api/jarvis/state");
      const payload = state.ok() ? await state.json() as SmokeState : null;
      if (
        payload?.session?.id === acceptedSession.id
        && payload.session.lifecycle_state !== "disconnected"
      ) {
        await page.request.delete(
          `/api/jarvis/live/sessions/${encodeURIComponent(acceptedSession.id)}?generation=${acceptedSession.generation}`,
        );
      }
    }
  }

  const finalState = await page.request.get("/api/jarvis/state");
  expect(finalState.ok()).toBe(true);
  const finalPayload = await finalState.json() as SmokeState;
  expect(finalPayload.current_log?.id).toBe(conversationId);
  const voiceMessages = finalPayload.current_log?.messages.filter((message) => message.modality === "voice") ?? [];
  expect(voiceMessages.some((message) => message.role === "user" && message.content.trim().length > 0)).toBe(true);
  expect(voiceMessages.some((message) => message.role === "assistant" && message.content.trim().length > 0)).toBe(true);
});

declare global {
  interface Window {
    __jarvisLiveSmoke: {
      diagnostics: string[];
      releaseAudio: () => Promise<void>;
    };
  }
}
