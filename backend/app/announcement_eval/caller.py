"""Independent Live caller. Its stdin receives audio only; never task events."""
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
    agenda = json.loads(os.environ["JARVIS_CALLER_AGENDA"])
    root = Path(os.environ["JARVIS_EVAL_ROOT"])
    instructions = (
        "You are a person having a spoken conversation with the assistant Jarvis. "
        "You initiate the conversation: begin with your first agenda request once "
        "the session starts, without waiting for a greeting. Speak English. "
        "Follow this conversational agenda: " + agenda +
        " Once you have requested the task and expressed your notification preference, "
        "do not ask about task progress or prompt for a notification. Allow at least "
        "twenty seconds of silence or unrelated conversation. Respond naturally to "
        "what you hear. Do not invent facts about task completion."
    )
    (root / "caller-prompt.txt").write_text(instructions, encoding="utf-8")
    started, closed = asyncio.Event(), asyncio.Event()
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
            (root / "caller-usage.json").write_text(json.dumps(usage, indent=2), encoding="utf-8")


if __name__ == "__main__":
    asyncio.run(main())
