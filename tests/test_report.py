"""Reporter math, against inputs whose answers are known by hand (rules.md §4.3).

This is the module that turns logs into the number on your resume. A bug here
does not crash anything -- it just prints a wrong number confidently, which is
the worst failure mode in the project.
"""

from __future__ import annotations

import csv
import json
from pathlib import Path

from engine.accounting import ledger as L
from engine.accounting.pricing import cost_usd
from engine.report.aggregate import gate_lift, render, summarize

RESULT_FIELDS = ("task_id", "solved", "attempts", "failure_type",
                 "prompt_tokens", "completion_tokens", "wall_ms", "model")


def _make_run(root: Path, run_id: str, rows: list[dict], ledger: list[L.LedgerRow] | None = None,
              gates: dict | None = None) -> Path:
    run = root / run_id
    run.mkdir(parents=True)
    with (run / "results.csv").open("w", newline="", encoding="utf-8") as h:
        w = csv.DictWriter(h, fieldnames=RESULT_FIELDS)
        w.writeheader()
        for r in rows:
            w.writerow(r)
    if ledger:
        L.append(run / "ledger.csv", ledger)
    (run / "config.json").write_text(json.dumps(
        gates or {"tester_gate": True, "reviewer_gate": True, "scout": True}))
    return run


def _row(task: str, solved: bool, attempts: int, failure: str = "",
         model: str = "openai/gpt-oss-20b") -> dict:
    return {"task_id": task, "solved": str(solved), "attempts": attempts,
            "failure_type": failure, "prompt_tokens": 100,
            "completion_tokens": 50, "wall_ms": 1000, "model": model}


def _call(task: str, model: str = "openai/gpt-oss-20b", n: int = 1) -> L.LedgerRow:
    return L.row_for(task, n, "builder", model, 1_000_000, 1_000_000, 10)


# --- counting -------------------------------------------------------------

def test_counts_and_rate(tmp_path: Path) -> None:
    run = _make_run(tmp_path, "r", [
        _row("a", True, 1), _row("b", False, 4, "failed_tests"),
        _row("c", True, 2), _row("d", False, 4, "patch_apply_error"),
    ])
    s = summarize(run)
    assert (s.tasks, s.solved) == (4, 2)
    assert s.success_rate == 0.5
    assert s.failures == {"failed_tests": 1, "patch_apply_error": 1}


def test_avg_attempts_counts_only_solved_tasks(tmp_path: Path) -> None:
    """Including failures would just measure the retry cap, not the effort."""
    run = _make_run(tmp_path, "r", [
        _row("a", True, 1), _row("b", True, 3),
        _row("c", False, 4, "failed_tests"),
    ])
    assert summarize(run).avg_attempts_on_solved == 2.0


# --- money ----------------------------------------------------------------

def test_usd_per_solved_is_total_spend_over_solved_count(tmp_path: Path) -> None:
    # Two calls at $0.375 each (1M in + 1M out on 20b), one task solved.
    per_call = cost_usd("openai/gpt-oss-20b", 1_000_000, 1_000_000)
    assert per_call == 0.375
    run = _make_run(tmp_path, "r",
                    [_row("a", True, 1), _row("b", False, 4, "failed_tests")],
                    ledger=[_call("a"), _call("b")])
    s = summarize(run)
    assert s.usd == 0.75
    assert s.usd_per_solved == 0.75  # 0.75 spent / 1 solved


def test_no_dollar_figure_when_a_call_was_unpriced(tmp_path: Path) -> None:
    """A missing price understates cost and looks like a bargain."""
    run = _make_run(tmp_path, "r", [_row("a", True, 1)],
                    ledger=[_call("a"), _call("a", model="mystery-model")])
    s = summarize(run)
    assert s.unpriced_calls == 1
    assert s.usd_per_solved is None
    assert "unpriced" in render([s])


def test_no_dollar_figure_when_nothing_solved(tmp_path: Path) -> None:
    """Dividing by zero is not '$0.00'."""
    run = _make_run(tmp_path, "r", [_row("a", False, 4, "failed_tests")],
                    ledger=[_call("a")])
    s = summarize(run)
    assert s.usd_per_solved is None
    assert "nothing solved" in render([s])


# --- refusals -------------------------------------------------------------

def test_a_stub_run_produces_no_table_at_all(tmp_path: Path) -> None:
    """A stub reports whatever it was scripted to report (rules.md §4.1.1)."""
    run = _make_run(tmp_path, "r", [_row("a", True, 1, model="stub")])
    out = render([summarize(run)])
    assert "No table produced" in out
    assert "1/1" not in out


def test_m1_refuses_when_the_arms_ran_different_task_counts(tmp_path: Path) -> None:
    """Same tasks, same model, same prompts -- or it is not an A/B."""
    off = summarize(_make_run(tmp_path, "off", [_row("a", False, 1, "failed_tests")]))
    on = summarize(_make_run(tmp_path, "on", [_row("a", True, 1), _row("b", True, 1)]))
    assert "different task counts" in gate_lift(off, on)


def test_m1_states_the_headline_with_counts_beside_percentages(tmp_path: Path) -> None:
    off = summarize(_make_run(
        tmp_path, "off",
        [_row("a", False, 1, "failed_tests"), _row("b", False, 1, "failed_tests"),
         _row("c", True, 1), _row("d", False, 1, "failed_tests")],
        gates={"tester_gate": False, "reviewer_gate": False, "scout": True}))
    on = summarize(_make_run(
        tmp_path, "on",
        [_row("a", True, 2), _row("b", False, 4, "failed_tests"),
         _row("c", True, 1), _row("d", True, 3)],
        ledger=[_call("a")]))
    line = gate_lift(off, on)
    assert "1/4 (25%)" in line
    assert "3/4 (75%)" in line
    assert "+50%" in line


def test_render_shows_counts_not_just_percentages(tmp_path: Path) -> None:
    run = _make_run(tmp_path, "r", [_row("a", True, 1), _row("b", False, 4, "failed_tests")])
    out = render([summarize(run)])
    assert "1/2 (50%)" in out
