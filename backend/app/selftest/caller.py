"""Simulated user: an independent GPT-Live session that talks to Jarvis.

Its stdin receives Jarvis's audio only, never task events, expected answers or
grading rules. After the call it answers a short private questionnaire as the
user, which the reviewing agent uses as evidence.
"""
import asyncio
import base64
import json
import os
import sys
import time
from pathlib import Path

import websockets
from app.config import settings


async def main():
    adaptive = json.loads(os.environ["JARVIS_CALLER_AGENDA"])
    root = Path(os.environ["JARVIS_EVAL_ROOT"])
    instructions = caller_instructions(adaptive)
    (root / "caller-prompt.txt").write_text(instructions, encoding="utf-8")
    started, closed = asyncio.Event(), asyncio.Event()
    dialogue: list[list[str]] = []
    incoming = bytearray()
    usage = {"sessions_started": 0, "voice_usage": None, "backend": [], "cost": None}

    def emit(event):
        print(json.dumps(event), flush=True)

    async with websockets.connect("wss://api.openai.com/v1/live/sessions",
        additional_headers={"Authorization": "Bearer " + settings.openai_api_key}, max_size=16 * 1024 * 1024) as socket:
        await socket.send(json.dumps({"type": "session.start", "session": {
            "model": "gpt-live-1", "instructions": instructions,
            "audio": {"format": {"type": "audio/pcm", "rate": 24000}, "output": {"voice": "marin"}},
            "delegation": {"type": "responses", "responses": {"model": "gpt-5.6-luna",
                "reasoning": {"effort": "low"}, "service_tier": "priority", "parallel_tool_calls": False,
                "tools": [], "instructions": "Support the caller's conversational agenda. Be concise."}}
        }}))

        async def receive_input():
            while not closed.is_set():
                line = await asyncio.to_thread(sys.stdin.readline)
                if not line:
                    await socket.send(json.dumps({"type": "session.close"}))
                    return
                message = json.loads(line)
                if message["type"] == "close":
                    await socket.send(json.dumps({"type": "session.close"}))
                    return
                incoming.extend(base64.b64decode(message["audio"]))
                if len(incoming) > 24000 * 2 * 4:
                    raise RuntimeError("Caller input relay exceeds four seconds; timing invalid")

        async def audio_clock():
            await started.wait()
            next_tick = time.monotonic()
            while not closed.is_set():
                next_tick += .02
                count = min(960, len(incoming))
                frame = bytes(incoming[:count]) + bytes(960 - count)
                del incoming[:count]
                await socket.send(json.dumps({"type": "session.input_audio.append",
                    "audio": base64.b64encode(frame).decode()}))
                await asyncio.sleep(max(0, next_tick - time.monotonic()))

        tasks = [asyncio.create_task(receive_input()), asyncio.create_task(audio_clock())]
        try:
            async for raw in socket:
                event = json.loads(raw)
                emit(event)
                if event["type"] in {"session.input_transcript.delta", "session.output_transcript.delta"}:
                    speaker = "Jarvis" if event["type"] == "session.input_transcript.delta" else "You"
                    if dialogue and dialogue[-1][0] == speaker:
                        dialogue[-1][1] += event.get("delta") or ""
                    else:
                        dialogue.append([speaker, event.get("delta") or ""])
                if event["type"] == "session.started":
                    usage["sessions_started"] += 1
                    usage["session_id"] = event["session"]["id"]
                    started.set()
                elif event["type"] in {"session.usage.updated", "session.closed"}:
                    usage["voice_usage"] = event.get("usage", usage["voice_usage"])
                    if event["type"] == "session.closed":
                        closed.set()
                        break
                elif event["type"] == "response.event" and event.get("event", {}).get("type") == "response.completed":
                    response = event["event"]["response"]
                    usage["backend"].append({"id": response["id"], "usage": response.get("usage")})
                elif event["type"] == "error":
                    raise RuntimeError("Simulated caller provider rejected a command: " + json.dumps(event))
                for task in tasks:
                    if task.done():
                        task.result()
        finally:
            closed.set()
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            usage["finalized"] = any(event.get("type") == "session.closed" for event in [locals().get("event", {})])
            if dialogue:
                try:
                    answers, questionnaire_usage = await questionnaire(adaptive, dialogue)
                    (root / "caller-questionnaire.json").write_text(json.dumps(answers, indent=2), encoding="utf-8")
                    usage["questionnaire"] = questionnaire_usage
                except Exception as error:  # evidence only; never fails the recorded call
                    usage["questionnaire_error"] = type(error).__name__
            (root / "caller-usage.json").write_text(json.dumps(usage, indent=2), encoding="utf-8")


def caller_instructions(adaptive: dict) -> str:
    return (
        f"You are {adaptive.get('persona') or 'a busy person'} having a spoken conversation with your assistant Jarvis. "
        "You start the conversation: say your first request as soon as the session starts. Speak English, casually, "
        "like a real person on the phone: short sentences, sometimes thinking aloud. "
        "Your goals for this conversation: " + adaptive["agenda"] + " "
        "Only pursue these goals; do not invent facts about tasks or their results. React honestly to what you hear: "
        "if something is unclear, ask; if you did not catch something, say so. When your goals are done, wind down "
        "naturally and then stay quiet."
    )


async def questionnaire(adaptive: dict, dialogue: list[list[str]]) -> tuple[dict, dict]:
    import httpx
    schema = {"type": "object", "properties": {
        "got_what_i_wanted": {"type": "boolean"}, "naturalness_1_to_5": {"type": "integer"},
        "missed_or_unclear": {"type": "string"}, "annoying_or_repeated": {"type": "string"},
        "comment": {"type": "string"}},
        "required": ["got_what_i_wanted", "naturalness_1_to_5", "missed_or_unclear", "annoying_or_repeated", "comment"],
        "additionalProperties": False}
    transcript = "\n".join(f"{speaker}: {text.strip()}" for speaker, text in dialogue if text.strip())
    async with httpx.AsyncClient(timeout=60, trust_env=False) as client:
        response = await client.post(settings.openai_live_base_url.rstrip("/") + "/responses",
            headers={"Authorization": "Bearer " + settings.openai_api_key}, json={
                "model": "gpt-5.6-luna", "reasoning": {"effort": "low"}, "store": False,
                "instructions": ("You were the user in this phone conversation with your assistant Jarvis. Your goals were: "
                    + adaptive["agenda"] + " Answer honestly as that user, from what you heard."),
                "input": transcript,
                "text": {"format": {"type": "json_schema", "name": "caller_questionnaire", "strict": True, "schema": schema}}})
        response.raise_for_status()
        raw = response.json()
    text = "".join(c.get("text", "") for o in raw.get("output", []) if o.get("type") == "message"
        for c in o.get("content", []) if c.get("type") == "output_text")
    return json.loads(text), {"request_id": raw.get("id"), "usage": raw.get("usage")}


if __name__ == "__main__":
    asyncio.run(main())
