"""Append-only JSONL event log -- the single source of truth (TAD §4, ADR-1).

Build week: 3, retrofitted into a working engine. The engine had to be provably
correct from results.csv alone before observability was layered on; that is now
true, which is why this arrives in week 3 rather than week 1.

Every observable fact flows through this file. No component reads another's
internal state: if the UI needs something, the engine emits an event for it
(rules.md §0.2). One reducer over this log drives the text feed, the garage
scene, and the replay scrubber -- live and historical are the same code path,
which is what buys FR-26..FR-29 almost free.

**Payloads carry POINTERS, not blobs** (ADR-5). Prompts, diffs and test output
live in `attempts/`; events reference them by run-relative path. That keeps the
stream small enough to tail and the UI fast.

**This writer fails LOUD** (rules.md §3.1). Everywhere else in the engine a
failure becomes data; here, if events cannot be written the run is worthless,
so we crash rather than continue blind. It is the one place death beats
degradation.
"""

from __future__ import annotations

import json
import os
import threading
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator

SCHEMA_VERSION = 1
LOG_NAME = "events.jsonl"

# The closed set, frozen at v:1 (ADR-10). Consumers MUST ignore unknown types
# rather than throw, which is what leaves room to add one without breaking
# every replay file already on disk.
EVENT_TYPES = frozenset({
    "run_started",
    "task_started",
    "agent_activated",
    "agent_done",
    "handoff",
    "context_pack_ready",
    "patch_produced",
    "patch_apply_error",
    "tests_run",
    "gate_verdict",
    "retry",
    "shipped",
    "task_failed",
    "budget_exceeded",
    "cost_tick",
    "run_finished",
})

AGENTS = frozenset({
    "orchestrator", "scout", "builder", "tester", "reviewer", "scribe", "system",
})


class EventLogError(RuntimeError):
    """The log could not be written. Fatal by design -- see the module docstring."""


def _now() -> str:
    """ISO-8601 UTC with milliseconds, Z-suffixed, as the schema specifies."""
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.") + \
        f"{datetime.now(timezone.utc).microsecond // 1000:03d}Z"


@dataclass
class EventLog:
    """Writer for one run's events.jsonl.

    `seq` is a per-run monotonic integer, so events have a total order even when
    two timestamps collide. The scrubber scrubs over `seq` and displays `ts`.

    Flushed per event: a killed run's log must be valid up to its last line.
    """

    path: Path
    run_id: str
    _seq: int = field(default=0, init=False)
    _lock: threading.Lock = field(default_factory=threading.Lock, init=False)

    @classmethod
    def for_run(cls, run_dir: Path, run_id: str) -> "EventLog":
        return cls(path=Path(run_dir) / LOG_NAME, run_id=run_id)

    def emit(
        self,
        type: str,
        agent: str = "system",
        task_id: str | None = None,
        **payload: Any,
    ) -> dict[str, Any]:
        """Append one event. Returns it, mostly so tests can assert on it.

        An unknown `type` raises: the closed set is the contract the frontend
        reducer is written against, and a typo would surface there as an event
        silently ignored rather than as an error here.
        """
        if type not in EVENT_TYPES:
            raise EventLogError(
                f"{type!r} is not in the v{SCHEMA_VERSION} event set. Add it to "
                f"EVENT_TYPES deliberately (ADR-10: the schema is frozen; "
                f"consumers ignore unknown types)."
            )
        if agent not in AGENTS:
            raise EventLogError(f"{agent!r} is not one of {sorted(AGENTS)}")

        with self._lock:
            self._seq += 1
            event = {
                "v": SCHEMA_VERSION,
                "ts": _now(),
                "seq": self._seq,
                "run_id": self.run_id,
                "task_id": task_id,
                "agent": agent,
                "type": type,
                "payload": payload,
            }
            self._write(event)
        return event

    def _write(self, event: dict[str, Any]) -> None:
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with self.path.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(event, ensure_ascii=False) + "\n")
                handle.flush()
                os.fsync(handle.fileno())  # survive a hard kill, not just an exit
        except OSError as exc:
            raise EventLogError(
                f"cannot write {self.path}: {exc}. A run whose events cannot be "
                f"written is worthless -- refusing to continue blind."
            ) from exc


def read_events(run_dir: Path, after_seq: int = 0) -> Iterator[dict[str, Any]]:
    """Stream a run's events, optionally only those after `after_seq`.

    Tolerates a truncated final line: a killed run leaves a partial write, and
    every complete line before it is still valid data (ADR-1).
    """
    path = Path(run_dir) / LOG_NAME
    if not path.is_file():
        return
    with path.open(encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                continue  # torn final line from a hard kill
            if event.get("seq", 0) > after_seq:
                yield event
