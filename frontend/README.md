# Frontend

This folder contains the Next.js frontend for AI Cockpit. It is the authenticated web UI that talks to the FastAPI backend over cookie-authenticated requests and provides the chat, knowledge, settings, generated-app, and placeholder background surfaces.

## Development

From this folder:

```bash
npm install
npm run dev
```

The dev server runs on `http://localhost:3000` and proxies API requests to the backend on `http://localhost:8000`.

Useful commands:

- `npm run dev` to start the Next.js dev server
- `npm run lint` to run ESLint
- `npm run test` to run the Vitest suite

## Main Surfaces

- `app/(app)/chat` — durable conversation UI with Agent and Council chat flows, inline agent execution, archive controls, branching, and limited tools
- `app/(app)/background` — placeholder surface explaining that background runs are disabled
- `app/(app)/knowledge` — reviewed memory queue and approved knowledge browser
- `app/(app)/settings` — model defaults for future sessions and the default agent-loop model
- `app/(app)/workspace/apps` — generated app management views
- `app/apps` — live generated app runtime routes
- `app/login` — authentication entrypoint

## Key Modules

- `lib/api.ts` — typed browser client for the backend APIs
- `lib/hooks.ts` — stateful hooks such as `useAuth` and `useChat`
- `lib/chat-history.ts` — maps conversation artifacts and events into chat-ready message data
- `components/` — shared UI building blocks for chat, navigation, and generated app surfaces

## Current UI Shape

- New chat starts in Agent mode and exposes only an Agent / Council choice before the first turn.
- Detailed model defaults live in Settings rather than on the chat start surface.
- The background page is intentionally a placeholder while all agent work stays inside chat.

## Testing Notes

The frontend uses:

- ESLint for static checks
- Vitest for unit and hook tests
- jsdom for component-oriented test execution

When backend behavior changes, update the corresponding types in `lib/api.ts` and any focused tests under `lib/*.test.ts`.
