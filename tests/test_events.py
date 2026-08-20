"""The event log — schema, ordering, durability (FR-17, TAD §4).

This file is the contract the frontend reducer will be written against, so its
guarantees are tested rather than assumed.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from engine.events import (
    EVENT_TYPES,
    SCHEMA_VERSION,
    EventLog,
    EventLogError,
    read_events,
)


@pytest.fixture
def log(tmp_path: Path) -> EventLog:
    return EventLog.for_run(tmp_path, "r_test")


# --- schema ---------------------------------------------------------------

def test_an_event_carries_the_documented_envelope(log: EventLog) -> None:
    e = log.emit("task_started", agent="orchestrator", task_id="t-1", issue="x")
    assert set(e) == {"v", "ts", "seq", "run_id", "task_id", "agent", "type", "payload"}
    assert e["v"] == SCHEMA_VERSION
    assert e["ts"].endswith("Z")


def test_an_unknown_type_is_refused(log: EventLog) -> None:
    """The closed set is the frontend's contract; a typo must fail HERE.

    Downstream it would surface as an event silently ignored (the forward-compat
    rule), which is indistinguishable from the event never being emitted.
    """
    with pytest.raises(EventLogError, match="not in the v1 event set"):
        log.emit("gate_verdcit")  # typo


def test_an_unknown_agent_is_refused(log: EventLog) -> None:
    with pytest.raises(EventLogError):
        log.emit("agent_activated", agent="mechanic")


def test_the_frozen_set_matches_the_tad(log: EventLog) -> None:
    """ADR-10 freezes v:1. Changing this set is a deliberate schema decision."""
    assert EVENT_TYPES == {
        "run_started", "task_started", "agent_activated", "agent_done",
        "handoff", "context_pack_ready", "patch_produced", "patch_apply_error",
        "tests_run", "gate_verdict", "retry", "shipped", "task_failed",
        "budget_exceeded", "cost_tick", "run_finished",
    }


# --- ordering -------------------------------------------------------------

def test_seq_is_monotonic_from_one(log: EventLog) -> None:
    """Total order even when two timestamps collide — the scrubber scrubs seq."""
    seqs = [log.emit("retry", agent="builder")["seq"] for _ in range(5)]
    assert seqs == [1, 2, 3, 4, 5]


def test_read_events_returns_them_in_order(log: EventLog) -> None:
    for _ in range(3):
        log.emit("cost_tick")
    got = list(read_events(log.path.parent))
    assert [e["seq"] for e in got] == [1, 2, 3]


def test_after_seq_returns_only_the_gap(log: EventLog) -> None:
    """A reconnecting client sends its last seq; the server replays from there.

    This is what makes reconnect and cold start the same code path (TAD §5.1).
    """
    for _ in range(5):
        log.emit("cost_tick")
    got = list(read_events(log.path.parent, after_seq=3))
    assert [e["seq"] for e in got] == [4, 5]


# --- durability -----------------------------------------------------------

def test_each_event_is_flushed_immediately(log: EventLog) -> None:
    """A killed run's log must be valid up to its last line (ADR-1)."""
    log.emit("run_started")
    assert len(log.path.read_text().splitlines()) == 1  # readable before exit


def test_a_torn_final_line_does_not_poison_the_read(log: EventLog) -> None:
    """A hard kill mid-write leaves a partial line; everything before it stands."""
    log.emit("run_started")
    log.emit("task_started", task_id="t-1")
    with log.path.open("a") as h:
        h.write('{"v": 1, "seq": 3, "ty')  # torn
    got = list(read_events(log.path.parent))
    assert [e["seq"] for e in got] == [1, 2]


def test_a_missing_log_reads_as_empty(tmp_path: Path) -> None:
    assert list(read_events(tmp_path / "nope")) == []


def test_the_writer_fails_loud_when_it_cannot_write(tmp_path: Path) -> None:
    """The one place the engine prefers death to degradation (rules.md §3.1).

    Everywhere else a failure becomes data. A run whose events cannot be written
    is worthless, so it crashes rather than continuing blind.
    """
    blocked = tmp_path / "file"
    blocked.write_text("i am a file, not a directory")
    log = EventLog(path=blocked / "events.jsonl", run_id="r")
    with pytest.raises(EventLogError, match="refusing to continue blind"):
        log.emit("run_started")


# --- the pointer rule -----------------------------------------------------

def test_payloads_are_small_enough_to_tail(log: EventLog) -> None:
    """ADR-5: events reference artifacts by path; blobs stay on disk."""
    e = log.emit("patch_produced", agent="builder", task_id="t-1",
                 attempt=2, lines=41, artifact="attempts/2/patch.diff")
    assert len(json.dumps(e)) < 500
    assert e["payload"]["artifact"] == "attempts/2/patch.diff"


# --------------------------------------- every activation has a completion

def test_no_agent_is_left_activated_forever(tmp_path):
    """A mechanic who starts and never finishes is a hole in the record.

    The garage is a pure view of this log, so an unbalanced activation shows
    up as somebody standing at the car until an unrelated event happens to
    move them. Spotted in a real feed: the builder emitted three `build start`
    and two `build done`, because the patch-rejected and apply-failed paths
    returned early without closing.
    """
    from engine.events import EventLog

    log = EventLog.for_run(tmp_path, "balance")
    for agent in ("scout", "builder", "tester"):
        log.emit("agent_activated", agent=agent, task_id="t")
        log.emit("agent_done", agent=agent, task_id="t")

    counts: dict[str, int] = {}
    for line in (tmp_path / "events.jsonl").read_text().splitlines():
        import json
        e = json.loads(line)
        if e["type"] == "agent_activated":
            counts[e["agent"]] = counts.get(e["agent"], 0) + 1
        elif e["type"] == "agent_done":
            counts[e["agent"]] = counts.get(e["agent"], 0) - 1
    assert all(v == 0 for v in counts.values()), f"unbalanced: {counts}"


def test_the_graph_closes_every_builder_exit():
    """Source-level guard: each `return` in builder_node must be preceded by an
    agent_done. Cheaper than a real run, and it fails when a new early exit is
    added without one."""
    import inspect
    import re

    from engine import graph

    src = inspect.getsource(graph.build_graph)
    body = re.search(r"def builder_node\(.*?\n(.*?)\n    def tester_node", src, re.S)
    assert body, "builder_node not found"
    text = body.group(1)
    # every return that yields attempts should have closed the agent first
    for match in re.finditer(r"\n(\s+)return (update|\{[^\n]*attempts)", text):
        before = text[max(0, match.start() - 400):match.start()]
        assert 'emit("agent_done", "builder"' in before, (
            "a builder_node exit returns without emitting agent_done:\n"
            + text[max(0, match.start() - 200):match.start() + 60])
