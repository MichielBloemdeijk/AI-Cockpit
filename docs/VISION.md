# Vision: AI Cockpit / Jarvis

This document records the owner's vision, direction and intentions for AI
Cockpit and Jarvis. Agents use it for strategic, architectural, design and other
major subjective decisions. It deliberately does not list or classify features:
[ARCHITECTURE.md](ARCHITECTURE.md) and the design documents describe what exists
and how it works.

## Why it exists

AI Cockpit / Jarvis is a long-term personal project: an agent harness that keeps
improving, and may learn over time. It uses the latest models and tools to build
a personal AI assistant that can do as much as possible, like Iron Man's JARVIS.

## North Star

A zero-friction assistant that is always available, quick and needs no
maintenance. It can do anything non-physical that a human personal assistant can
do, and more. Interacting with it should feel like talking to a real butler or
personal assistant, not to a standard AI assistant:

| Dimension | Typical AI assistant | Skilled human personal assistant |
|---|---|---|
| Default posture | Eager to help | Quietly available |
| Verbosity | Explanatory, comprehensive | Concise, economical |
| Initiative | Proactively offers assistance | Exercises restrained initiative |
| Assumptions | Assumes more help is welcome | Assumes competence and autonomy |
| Politeness | Explicit, enthusiastic, reassuring | Understated, natural, tactful |
| Social awareness | Optimizes for usefulness | Optimizes for appropriateness |
| Questions | Frequently asks follow-ups | Asks only when necessary |
| Confidence | Often announces what it can do | Quietly handles what it can |
| Relationship | Helpful service provider | Trusted, discreet professional |
| Conversation | Tries to keep interaction going | Comfortable letting it end |

The right-hand column is the target.

## Who it is for

For now, only the owner and the owner's personal wishes, on the owner's own
devices. A public release may be considered one day if the project reaches a good
state, but it is not a short-term goal. Do not let hypothetical other users
drive current decisions.

## Direction

The system keeps expanding and improving. Parts are replaced, added and
fine-tuned, all to move closer to the North Star. No component is sacred: judge
each one by how much it contributes to that goal, not by how much effort went
into it.

## Principles and trade-offs

- **Cost is a balance.** The budget is finite. Minor improvements don't justify
  higher costs; major ones do. If a better model or service brings Jarvis
  significantly closer to the North Star, it is worth paying for. Otherwise,
  keep costs low. Weigh this case by case.
- **Coherent current architecture over compatibility.** Development data is
  disposable (see the root `AGENTS.md`).
- **Security is secondary for a personal project** reached only over the owner's
  Tailscale network: fix issues that are severe or trivial to fix, and document
  the rest.

## Non-goals

- Becoming another standard AI assistant.

## How agents use this document

- **Align significant choices.** Before a significant architectural, design or
  subjective decision (including removing or replacing a capability, or changing
  how Jarvis behaves toward the owner), check it against this document.
- **Contradictions: remind, then ask.** When an instruction or wish from the
  owner contradicts this document, first point out the difference, then ask
  whether to proceed and whether the document should change. Never silently
  replace the vision or act against it without permission.
- **When the vision doesn't settle it, ask.** Don't invent goals or decide
  subjective questions alone.
- **Check real usage before calling something unused.** Look at every data
  source and its history: older databases (for example
  `backend/data/ai_cockpit.db`), git history and logs, not just the current
  database. A feature was once removed as "unused" because only the newest
  database was checked, although it had been in active use for months.

## Keeping it current

The owner owns this document. Agents may propose edits, for example when a
decision or instruction conflicts with it, but change it only with the owner's
explicit approval. Feature status and implementation detail belong in the
architecture and design documents, not here.
