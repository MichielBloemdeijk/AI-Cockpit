# Frontend

The Next.js web and phone UI of AI Cockpit / Jarvis. It talks to the FastAPI
backend through same-origin `/api` requests that Next.js proxies to port 8000.
Read [AGENTS.md](AGENTS.md) first: this Next.js version differs from older ones.

## Commands

```bash
npm ci
npm run dev          # http://localhost:3000
npm test             # Vitest
npm run lint
npm run typecheck    # main app only
npm run build        # production build used by the personal host
npm run build:results  # the separate result-export app
```

When and why to run each is in [docs/VERIFICATION.md](../docs/VERIFICATION.md).
Test, smoke and acceptance builds write to their own `.next-<purpose>` folder;
lint, typecheck and Git ignore all of them.

## Routes

- `app/(app)/chat`: conversations in single-model (Chat) or Council mode, with
  branches and archive
- `app/(app)/jarvis`: the Jarvis voice workspace and phone UI
- `app/(app)/tasks` and `tasks/[taskId]`: background tasks and their trace
- `app/(app)/knowledge`: reviewed memory queue and approved knowledge (`wiki`
  shows the same page)
- `app/(app)/settings`: model defaults, phone linking, worker tools, wake
  calibration
- `app/(app)/workspace/apps` and `apps/[slug]`: installed apps, each in a
  sandboxed frame with the SDK bridge in `lib/app-platform.ts`
- `app/login`, `app/link-phone`: sign-in and phone pairing
- `background` and `voice` only redirect (to `/tasks` and `/jarvis`)

## Key modules

- `lib/api.ts`: typed client for the backend APIs
- `lib/hooks.ts`, `lib/use-conversation-*.ts`, `lib/conversation-feed.ts`:
  conversation list, detail, streaming and the committed timeline
- `lib/jarvis-live*.ts`, `lib/jarvis-voice-client.ts`: the Live session and its
  replayable state
- `lib/jarvis-wake-*.ts`, `lib/jarvis-hey-buddy-inference.ts`: browser wake
  detection
- `components/`: shared UI

When backend responses change, update the types in `lib/api.ts` and the focused
tests next to the module (`lib/*.test.ts`).
