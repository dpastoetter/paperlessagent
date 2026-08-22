#!/usr/bin/env python3
"""Watch data/inbox and run the ADK ingest pipeline for new scans."""

from __future__ import annotations

import argparse
import asyncio
import sys
import time
from pathlib import Path

from watchdog.events import FileSystemEventHandler
from watchdog.observers import Observer

# Allow running as `python scripts/watch_inbox.py` from repo root
ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from deepcatalog.config import ensure_data_dirs  # noqa: E402
from deepcatalog.runner import run_pipeline_on_path  # noqa: E402
from deepcatalog.settings import get_source_dir, load_settings  # noqa: E402
from deepcatalog.tools.filesystem import SUPPORTED_SUFFIXES as FILE_SUFFIXES  # noqa: E402


class InboxHandler(FileSystemEventHandler):
    def __init__(self, debounce_seconds: float = 1.5) -> None:
        super().__init__()
        self.debounce_seconds = debounce_seconds
        self._seen: dict[str, float] = {}
        self._queue: asyncio.Queue[str] | None = None
        self._loop: asyncio.AbstractEventLoop | None = None

    def attach(self, loop: asyncio.AbstractEventLoop, queue: asyncio.Queue[str]) -> None:
        self._loop = loop
        self._queue = queue

    def on_created(self, event) -> None:  # noqa: ANN001
        if event.is_directory:
            return
        path = Path(event.src_path)
        if path.suffix.lower() not in FILE_SUFFIXES:
            return
        # Wait briefly for copy/write to finish
        now = time.time()
        last = self._seen.get(str(path), 0)
        if now - last < self.debounce_seconds:
            return
        self._seen[str(path)] = now
        if self._loop and self._queue:
            self._loop.call_soon_threadsafe(self._queue.put_nowait, str(path.resolve()))


async def worker(queue: asyncio.Queue[str]) -> None:
    while True:
        path = await queue.get()
        # Extra settle time for scanners that write slowly
        await asyncio.sleep(1.0)
        p = Path(path)
        if not p.exists():
            queue.task_done()
            continue
        print(f"[watch] processing {p}")
        try:
            result = await run_pipeline_on_path(str(p))
            print(f"[watch] done: {result.get('reply') or result.get('status')}")
        except Exception as exc:  # noqa: BLE001
            print(f"[watch] error for {p}: {exc}")
        finally:
            queue.task_done()


async def main(poll_existing: bool) -> None:
    ensure_data_dirs()
    load_settings()
    inbox_dir = get_source_dir()
    inbox_dir.mkdir(parents=True, exist_ok=True)
    queue: asyncio.Queue[str] = asyncio.Queue()
    loop = asyncio.get_running_loop()
    handler = InboxHandler()
    handler.attach(loop, queue)

    if poll_existing:
        for path in sorted(inbox_dir.iterdir()):
            if path.is_file() and path.suffix.lower() in FILE_SUFFIXES:
                await queue.put(str(path.resolve()))

    observer = Observer()
    observer.schedule(handler, str(inbox_dir), recursive=False)
    observer.start()
    print(f"[watch] watching {inbox_dir}")

    worker_task = asyncio.create_task(worker(queue))
    try:
        while True:
            await asyncio.sleep(1)
    except asyncio.CancelledError:
        pass
    finally:
        observer.stop()
        observer.join(timeout=5)
        worker_task.cancel()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Watch inbox and process new scans")
    parser.add_argument(
        "--process-existing",
        action="store_true",
        help="Also process files already in the inbox at startup",
    )
    args = parser.parse_args()
    try:
        asyncio.run(main(poll_existing=args.process_existing))
    except KeyboardInterrupt:
        print("\n[watch] stopped")
