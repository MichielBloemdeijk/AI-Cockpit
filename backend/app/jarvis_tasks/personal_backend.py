"""Gracefully stop the personal API/pool when the owning host requests it."""
import asyncio
import sys
from pathlib import Path
import uvicorn


async def run():
    stop_file = Path(sys.argv[1])
    server = uvicorn.Server(uvicorn.Config("app.main:app", host="127.0.0.1", port=8000, log_level="info"))

    async def watch():
        while not server.should_exit:
            if stop_file.exists():
                server.should_exit = True
                return
            await asyncio.sleep(.2)

    watcher = asyncio.create_task(watch())
    try:
        await server.serve()
    finally:
        watcher.cancel()
        await asyncio.gather(watcher, return_exceptions=True)


if __name__ == "__main__":
    asyncio.run(run())
