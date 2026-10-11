"""Render the fixed synthetic corpus using an explicitly authorized Audio API run."""
from __future__ import annotations

import argparse
from array import array
import asyncio
from datetime import datetime, timezone
from hashlib import sha256
import json
from pathlib import Path
import sys
import wave

import httpx

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend"))
from app.config import settings

MANIFEST = Path(__file__).with_name("audio-corpus-v1.json")
MODEL = "gpt-4o-mini-tts-2025-12-15"


def mono_16k(pcm: bytes) -> bytes:
    """Convert the API's 24 kHz mono S16LE to the 16 kHz input contract."""
    if len(pcm) % 2 or not 4800 <= len(pcm) <= 24_000 * 2 * 30:
        raise ValueError("Provider PCM is empty, malformed or longer than 30 seconds")
    source = array("h")
    source.frombytes(pcm)
    if sys.byteorder != "little":
        source.byteswap()
    if max(abs(value) for value in source) < 50:
        raise ValueError("Provider PCM contains no usable speech")
    converted = array("h")
    for index in range(0, len(source) - 2, 3):
        converted.extend((source[index], (source[index + 1] + source[index + 2]) // 2))
    if sys.byteorder != "little":
        converted.byteswap()
    return converted.tobytes()


async def render(output: Path, concurrency: int) -> None:
    if output.exists():
        raise ValueError("A fresh output directory is required")
    if not settings.openai_api_key.strip():
        raise ValueError("An existing authorized OPENAI_API_KEY is required")
    corpus = json.loads(MANIFEST.read_text(encoding="utf-8"))
    output.mkdir(parents=True)
    semaphore = asyncio.Semaphore(concurrency)
    proofs = []
    async with httpx.AsyncClient(timeout=60) as client:
        async def speech(text, voice):
            async with semaphore:
                response = await client.post("https://api.openai.com/v1/audio/speech",
                    headers={"Authorization": f"Bearer {settings.openai_api_key}"},
                    json={"model": MODEL, "voice": voice, "input": text, "response_format": "pcm",
                        "instructions": "Speak clearly and naturally in English at a normal conversational pace."})
            if response.status_code != 200:
                raise ValueError(f"Audio API rejected corpus generation: HTTP {response.status_code}")
            return mono_16k(response.content), response.headers.get("x-request-id")
        wake = {voice: await speech(corpus["wake"], voice) for voice in ("alloy", "onyx")}
        async def sample(index, case):
            voice = ("alloy", "onyx")[index % 2]
            command, request_id = await speech(case["text"], voice)
            hashes = {}
            for part, pcm in (("wake", wake[voice][0]), ("command", command)):
                path = output / f"{case['id']}-{part}.wav"
                with wave.open(str(path), "wb") as stream:
                    stream.setparams((1, 2, 16_000, 0, "NONE", "not compressed"))
                    stream.writeframes(pcm)
                hashes[part] = sha256(path.read_bytes()).hexdigest()
            proofs.append({"id": case["id"], "voice": voice, "request_id": request_id,
                "wake_request_id": wake[voice][1], "wav_sha256": hashes, "command_samples": len(command) // 2})
            print(f"Rendered {case['id']}: {len(command) // 2} samples", flush=True)
        await asyncio.gather(*(sample(index, case) for index, case in enumerate(corpus["cases"])))
    (output / "render.json").write_text(json.dumps({"schema": "jarvis-audio-render.v1", "model": MODEL,
        "renderer": "OpenAI Audio API; generated test speech", "corpus_sha256": sha256(MANIFEST.read_bytes()).hexdigest(),
        "finished_at": datetime.now(timezone.utc).isoformat(), "samples": sorted(proofs, key=lambda row: row["id"])}, indent=2) + "\n",
        encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--live", action="store_true", required=True, help="Authorize this billable corpus-generation run")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--concurrency", type=int, choices=range(1, 4), default=1)
    args = parser.parse_args()
    try:
        asyncio.run(render(args.output.resolve(), args.concurrency))
    except (ValueError, OSError, httpx.HTTPError) as exc:
        # Report types/status only: provider response bodies and auth headers stay private.
        print(str(exc) if isinstance(exc, ValueError) else type(exc).__name__, file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
