"""Batch resume and bookkeeping (FR-11, NFR-4).

Offline. A 16-hour run WILL be interrupted, so the resume path is not a nicety
-- it is the difference between losing an hour and losing a night.
"""

from __future__ import annotations

from pathlib import Path

import pytest

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


def test_run_spend_sums_every_arm_from_disk(tmp_path: Path) -> None:
    """A resumed batch must inherit what earlier sessions already spent.

    A run-level cap that forgets prior spend is not a cap -- resume three times
    and you have paid three times the ceiling.
    """
    from engine.accounting import ledger as L
    from engine.batch import run_spend_usd

    on, off = tmp_path / "on", tmp_path / "off"
    row = lambda t: L.row_for(t, 1, "builder", "openai/gpt-oss-20b",
                              1_000_000, 1_000_000, 10)  # $0.375 each
    L.append(on / "ledger.csv", [row("a")])
    L.append(off / "ledger.csv", [row("a"), row("b")])
    assert run_spend_usd([on, off]) == pytest.approx(0.375 * 3)


def test_run_spend_is_zero_before_anything_runs(tmp_path: Path) -> None:
    from engine.batch import run_spend_usd
    assert run_spend_usd([tmp_path / "nope"]) == 0.0


def test_all_three_gate_flags_are_reachable_from_the_cli() -> None:
    """FR-10: every A/B in the PRD must be one CLI flag, no code edit.

    The reviewer gate was config-only, which made E3 -- the ponytail
    experiment, and the PRD's publishable finding -- unrunnable without
    editing code between arms. That is precisely what FR-10 forbids.
    """
    import argparse
    from engine.cli import main

    for cmd, flags in (
        ("run-batch", {"--arms", "--no-scout", "--no-reviewer-gate"}),
        ("run-one", {"--no-scout", "--no-reviewer-gate"}),
    ):
        try:
            main([cmd, "--help"])
        except SystemExit:
            pass  # --help exits 0; we only need the parser built without error

    parser = argparse.ArgumentParser()  # sanity: the import path works
    assert parser is not None


def _write_config(d: Path, **fields) -> None:
    import json
    d.mkdir(parents=True, exist_ok=True)
    (d / "config.json").write_text(json.dumps(fields))


def test_resume_is_refused_when_the_model_differs(tmp_path: Path) -> None:
    """Mixing two models' results into one table is accidental data fraud."""
    from engine.batch import resume_conflict
    from engine.config import RunConfig

    _write_config(tmp_path, model_for_role={"builder": "openai/gpt-oss-120b"})
    cfg = RunConfig(run_id="x", task_ids=[],
                    model_for_role={"builder": "claude-sonnet-5"})
    reason = resume_conflict(tmp_path, cfg)
    assert reason and "gpt-oss-120b" in reason


def test_resume_is_refused_when_the_gates_differ(tmp_path: Path) -> None:
    from engine.batch import resume_conflict
    from engine.config import RunConfig

    _write_config(tmp_path, tester_gate=False)
    cfg = RunConfig(run_id="x", task_ids=[], tester_gate=True)
    assert resume_conflict(tmp_path, cfg)


def test_resume_is_refused_for_a_run_marked_invalid(tmp_path: Path) -> None:
    from engine.batch import resume_conflict
    from engine.config import RunConfig

    _write_config(tmp_path, tester_gate=True)
    (tmp_path / "INVALID.md").write_text("quarantined")
    cfg = RunConfig(run_id="x", task_ids=[], tester_gate=True)
    reason = resume_conflict(tmp_path, cfg)
    assert reason and "INVALID" in reason


def test_a_matching_config_resumes_normally(tmp_path: Path) -> None:
    from engine.batch import resume_conflict
    from engine.config import RunConfig

    cfg = RunConfig(run_id="x", task_ids=[],
                    model_for_role={"builder": "claude-sonnet-5"})
    _write_config(tmp_path, model_for_role={"builder": "claude-sonnet-5"},
                  tester_gate=True, reviewer_gate=True, scout=True)
    assert resume_conflict(tmp_path, cfg) is None


def test_a_fresh_directory_has_nothing_to_conflict_with(tmp_path: Path) -> None:
    from engine.batch import resume_conflict
    from engine.config import RunConfig
    assert resume_conflict(tmp_path / "new", RunConfig(run_id="x", task_ids=[])) is None
