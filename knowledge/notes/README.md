# Knowledge Base Notes

Drop approved markdown knowledge here. In the current POC this directory is a simple, file-backed knowledge layer that chat and background agents can inspect directly as normal documents.

## Structure

- `notes/` — General notes, research, ideas
- `projects/` — Per-project context (create subdirectories)
- `decisions/` — Key architectural decisions

## Current Behavior

- Approved knowledge items can be written here from the reviewed memory flow.
- This directory is intended to stay human-readable and easy to reorganize.
- There is no vector database or advanced retrieval layer in the current POC.

## Format

Use standard markdown. Front matter is supported:

```yaml
---
title: My Note
tags: [python, fastapi]
date: 2026-04-15
---
```
