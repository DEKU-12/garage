"""Batch resume and bookkeeping (FR-11, NFR-4).

Offline. A 16-hour run WILL be interrupted, so the resume path is not a nicety
-- it is the difference between losing an hour and losing a night.
"""

from __future__ import annotations

from pathlib import Path

from engine.batch import ARMS, append_result, completed_task_ids


def _row(task_id: str, solved: bool = True) -> dict:
    return {"task_id": task_id, "solved": str(solved), "attempts": 1,
            "failure_type": "", "prompt_tokens": 10, "completion_tokens": 5,
            "wall_ms": 100, "model": "openai/gpt-oss-20b"}


def test_no_results_file_means_nothing_is_done(tmp_path: Path) -> None:
    assert completed_task_ids(tmp_path / "results.csv") == set()


def test_completed_ids_come_back_for_resume(tmp_path: Path) -> None:
    csv_path = tmp_path / "results.csv"
    append_result(csv_path, _row("a"))
    append_result(csv_path, _row("b", solved=False))
    # Both count as done: a FAILED task is finished, not pending. Re-running it
    # would spend money to reproduce a result already recorded.
    assert completed_task_ids(csv_path) == {"a", "b"}


def test_appending_writes_the_header_once(tmp_path: Path) -> None:
    csv_path = tmp_path / "results.csv"
    append_result(csv_path, _row("a"))
    append_result(csv_path, _row("b"))
    lines = csv_path.read_text().strip().splitlines()
    assert len(lines) == 3
    assert lines[0].startswith("task_id,")
    assert sum(1 for line in lines if line.startswith("task_id,")) == 1


def test_each_row_lands_immediately_so_a_crash_loses_at_most_one_task(tmp_path: Path) -> None:
    csv_path = tmp_path / "results.csv"
    append_result(csv_path, _row("a"))
    assert completed_task_ids(csv_path) == {"a"}  # readable before the batch ends


def test_the_two_arms_differ_only_in_the_tester_gate(tmp_path: Path) -> None:
    assert ARMS["on"].tester_gate is True
    assert ARMS["off"].tester_gate is False


def test_arms_keep_separate_result_files(tmp_path: Path) -> None:
    """Resume must never let one arm's progress mask the other's."""
    on, off = tmp_path / "on" / "results.csv", tmp_path / "off" / "results.csv"
    append_result(on, _row("a"))
    assert completed_task_ids(on) == {"a"}
    assert completed_task_ids(off) == set()
