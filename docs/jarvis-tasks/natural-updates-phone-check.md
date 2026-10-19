# Natural task updates: final phone check (about 15 minutes)

This is the one manual step in the natural-updates plan. Everything else ran through agent self-tests on the browser and the Android emulator; see the [report](reports/2026-10-19-natural-notes-selftest.md).

This check covers what the emulator cannot:
- your own voice in a real room;
- the phone speaker and echo;
- the lock screen;
- Android/OEM background behaviour.

## Before you start (2 minutes)

1. **Restart the host so it picks up the new default.** Run `Jarvis.cmd Stop`, then `Jarvis.cmd Start`, and wait for **Ready**.
2. **Install the latest APK over the existing app.** Use the [Jarvis.apk](https://sarah.tail68fb8f.ts.net/api/jarvis/devices/android.apk) link and keep your pairing.
3. **Optional: confirm the mode.** While logged in, open `/api/jarvis/live/observer` on the host; it should show `"mode": "enabled"`.

## Checks

Use any small task, for example "write a short poem about coffee". Note the time of anything that goes wrong.

| # | What to do | What should happen |
|---|---|---|
| 1 | Ask for the task and add "tell me when it's done". Then stay quiet. | Within roughly 10 s of completion, Jarvis tells you once, naming the task and the result. No phone push follows for it. |
| 2 | Ask for a task with "tell me when it's done". When Jarvis starts its update, talk over it with something unrelated ("oh, wait, what's a good lunch?"). | Jarvis yields and answers you. Then, at a natural moment, it briefly re-offers the result once ("by the way, …"). |
| 3 | Ask for a task and say "don't tell me when it's done, I'll check myself". Later ask "is it done yet?" | Jarvis stays quiet when it finishes and answers your question accurately. It never claims it already told you. |
| 4 | Ask for a task with "tell me when it's done", then end the conversation right away (**Stop Live** or say goodbye). Wait until it has finished, then say "Hey Jarvis". | In the new conversation, Jarvis brings up the result unprompted within a few seconds. |
| 5 | Same as 4, but don't start a new conversation for a minute. | A phone notification arrives about 15 s after the conversation ended (if the result hadn't been heard). |
| 6 | Say "remind me in one minute to stand up", then chat about something else. | The reminder comes on time, once. |
| 7 | Put the phone on speaker in a quiet room and let Jarvis give a longer answer. | Its own voice through the speaker does not make it stop or "hear" you (echo). |
| 8 | During a task with "tell me when it's done", lock the phone and wait for completion. | You either hear the update (if Live keeps running on your phone) or get a notification after the conversation ends. You never get both. |

Optional:
- Repeat check 2 with a natural interruption of your own.
- Ask "how did that go?" without naming the task (Jarvis should know which one).
- With two tasks, say you want to know "if either fails". A failed task should be told promptly and politely, and a background retry briefly mentioned.

## Afterwards

- Open `/api/jarvis/diagnostics` on the host while logged in. Expect `"status": "ok"` and no `high` findings.
- Note for any failed check:
  - the check number;
  - the time;
  - what Jarvis said;
  - your phone model.

  An agent can then diagnose it from `.cockpit/jarvis/logs/` and the session records, and add a matching self-test scenario.
- **Rolling back:** set `JARVIS_CONVERSATION_OBSERVER_MODE=off` in `.env`, then run `Jarvis.cmd Stop` and `Jarvis.cmd Start`.

**Known limits that are not failures:**
- Jarvis cannot yet read a finished document's text aloud; it will say so.
- Two requests in very quick succession are occasionally merged or dropped.
