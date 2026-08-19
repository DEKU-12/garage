"""Follows events.jsonl and yields new events as they land (TAD §5.1).

Build week: 3.

Polls rather than watches: 150 ms is comfortably inside NFR-6's 250 ms
event-to-pixel budget, and a poll works identically on every filesystem and
over any mount a run directory might live on. The log is append-only, so
"what's new" is just "everything past the last seq I saw" -- no diffing, no
inotify, no state beyond an integer.

That integer is also why reconnect and cold start are the same code path: a
client that says `after_seq=143` and a client that says `after_seq=0` take the
identical route through this file.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import AsyncIterator

from engine.events import LOG_NAME, read_events

POLL_INTERVAL_S = 0.15
TERMINAL_TYPES = {"run_finished"}


async def tail_events(
    run_dir: Path,
    after_seq: int = 0,
    poll_interval_s: float = POLL_INTERVAL_S,
    stop_on_finish: bool = True,
) -> AsyncIterator[dict]:
    """Yield every event after `after_seq`, then keep yielding as they arrive.

    The catch-up read and the live tail are the same loop, so a client never
    sees a gap between "history" and "now" -- the seam that usually loses events
    on reconnect does not exist here.

    Ends after `run_finished` when `stop_on_finish`, so a finished run replays
    to completion and closes rather than holding a socket open forever.
    """
    seq = after_seq
    while True:
        found_any = False
        for event in read_events(run_dir, after_seq=seq):
            seq = event.get("seq", seq)
            found_any = True
            yield event
            if stop_on_finish and event.get("type") in TERMINAL_TYPES:
                return
        if not found_any:
            await asyncio.sleep(poll_interval_s)


def run_dirs(runs_root: Path) -> list[Path]:
    """Every directory under runs/ that has an event log, newest first."""
    root = Path(runs_root)
    if not root.is_dir():
        return []
    dirs = [d for d in root.iterdir() if d.is_dir() and (d / LOG_NAME).is_file()]
    return sorted(dirs, key=lambda d: (d / LOG_NAME).stat().st_mtime, reverse=True)
