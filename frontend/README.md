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
- `app/(app)/workspace/apps`: the list of installed apps
- `app/apps/[slug]`: one installed app in a sandboxed frame, with the SDK bridge
  in `lib/app-platform.ts`. It sits outside the `(app)` group so it has its own
  layout; `?collection=&key=` opens a record
- `app/login`, `app/link-phone`: sign-in and phone pairing
- `background` and `voice` only redirect (to `/tasks` and `/jarvis`)

## Key modules

- `lib/api/`: the typed backend client by domain (`auth`, `conversations`,
  `jarvis`, `tasks`, `knowledge`, `push`) over the one request transport in
  `http.ts`
- `lib/conversations/` and `lib/hooks.ts`: chat list, detail, streaming and the
  committed timeline (`feed.ts`, `use-detail.ts`, `use-actions.ts`)
- `lib/jarvis/`: the Live session (`live.ts`, `voice-session.ts`,
  `use-jarvis.ts`), its replayable state (`feed.ts`, `live-state.ts`) and browser
  wake detection (`wake-*.ts`, `hey-buddy-inference.ts`)
- `lib/task-status.ts`, `lib/task-trace.ts`, `lib/app-platform.ts`: shared task
  and app logic
- `components/screens/`: screens shared with the static `result-export` build;
  `components/`: other shared UI

When backend responses change, update the types in the matching `lib/api/*.ts`
file and the focused tests next to the module (`*.test.ts`).
