"""Run logs -> the M1..M3 tables (FR-31).

Build week: 2.

THE THIRD LAW: every number in the README, the resume line, or any report comes
out of this module over real run logs. Never typed by hand, never estimated,
never "approximately" (rules.md §0.3).

A pure function over files -- which is what makes NFR-1 true: any reported
number is regenerable from committed logs with one command.

Three refusals are deliberate:

- A run containing stub results reports NOTHING. A stub reports whatever it was
  scripted to report; letting it reach a table would be fabrication
  (rules.md §4.1.1).
- $/solved is withheld when any contributing call was unpriced. A missing price
  silently understates cost and looks like a bargain.
- $/solved is withheld when nothing was solved. Division by zero is not "$0.00",
  and "infinite" is not a number you put on a resume.
"""

from __future__ import annotations

import csv
import json
from dataclasses import dataclass, field
from pathlib import Path

from engine.accounting.ledger import read as read_ledger


@dataclass(frozen=True)
class RunSummary:
    """Everything measurable about one run, and what could not be measured."""

    run_id: str
    tasks: int
    solved: int
    is_stub: bool
    usd: float
    unpriced_calls: int
    attempts_on_solved: list[int]
    failures: dict[str, int] = field(default_factory=dict)
    gates: dict[str, bool] = field(default_factory=dict)

    @property
    def success_rate(self) -> float | None:
        return self.solved / self.tasks if self.tasks else None

    @property
    def usd_per_solved(self) -> float | None:
        """None when it cannot be stated honestly, rather than a wrong number."""
        if self.solved == 0 or self.unpriced_calls:
            return None
        return self.usd / self.solved

    @property
    def avg_attempts_on_solved(self) -> float | None:
        """M3: mean builder attempts on SOLVED tasks only.

        Averaging over failures too would just measure the retry cap.
        """
        if not self.attempts_on_solved:
            return None
        return sum(self.attempts_on_solved) / len(self.attempts_on_solved)


def summarize(run_dir: Path) -> RunSummary:
    """Fold one run directory into a summary. Reads only committed artifacts."""
    run_dir = Path(run_dir)
    results = run_dir / "results.csv"
    rows = []
    if results.is_file():
        with results.open(newline="", encoding="utf-8") as handle:
            rows = list(csv.DictReader(handle))

    solved_rows = [r for r in rows if r.get("solved") == "True"]
    failures: dict[str, int] = {}
    for row in rows:
        if row.get("solved") == "True":
            continue
        failures[row.get("failure_type") or "unknown"] = (
            failures.get(row.get("failure_type") or "unknown", 0) + 1
        )

    ledger = read_ledger(run_dir / "ledger.csv")
    gates: dict[str, bool] = {}
    config = run_dir / "config.json"
    if config.is_file():
        raw = json.loads(config.read_text())
        gates = {k: bool(raw.get(k)) for k in ("tester_gate", "reviewer_gate", "scout")}

    return RunSummary(
        run_id=run_dir.name,
        tasks=len(rows),
        solved=len(solved_rows),
        is_stub=any(r.get("model") == "stub" for r in rows),
        usd=sum(r.usd for r in ledger),
        unpriced_calls=sum(1 for r in ledger if not r.priced),
        attempts_on_solved=[int(r["attempts"]) for r in solved_rows],
        failures=failures,
        gates=gates,
    )


def _pct(summary: RunSummary) -> str:
    """Counts beside percentages, always (DESIGN.md §7)."""
    if summary.success_rate is None:
        return "--"
    return f"{summary.solved}/{summary.tasks} ({summary.success_rate:.0%})"


def _usd(summary: RunSummary) -> str:
    if summary.usd_per_solved is None:
        if summary.unpriced_calls:
            return f"unknown ({summary.unpriced_calls} unpriced calls)"
        return "n/a (nothing solved)"
    return f"${summary.usd_per_solved:.4f}"


def _attempts(summary: RunSummary) -> str:
    value = summary.avg_attempts_on_solved
    return "--" if value is None else f"{value:.1f}"


def render(summaries: list[RunSummary], title: str = "Results") -> str:
    """The markdown table. Refuses to render anything derived from a stub."""
    stubs = [s.run_id for s in summaries if s.is_stub]
    if stubs:
        return (
            f"# {title}\n\n"
            f"**No table produced.** These runs contain stub results: "
            f"{', '.join(stubs)}. A stub reports whatever it was scripted to "
            f"report, so nothing derived from one may appear in a report "
            f"(rules.md §4.1.1). Re-run against a real model.\n"
        )

    lines = [
        f"# {title}",
        "",
        "| Run | Gates | Solved | $/solved | Avg attempts |",
        "|---|---|---|---|---|",
    ]
    for s in summaries:
        gates = ", ".join(k for k, v in s.gates.items() if v) or "none"
        lines.append(
            f"| `{s.run_id}` | {gates} | {_pct(s)} | {_usd(s)} | {_attempts(s)} |"
        )

    failing = {k for s in summaries for k in s.failures}
    if failing:
        lines += ["", "## Failures by type", "",
                  "| Run | " + " | ".join(sorted(failing)) + " |",
                  "|---" * (len(failing) + 1) + "|"]
        for s in summaries:
            cells = " | ".join(str(s.failures.get(k, 0)) for k in sorted(failing))
            lines.append(f"| `{s.run_id}` | {cells} |")

    lines += ["", f"*Generated by report/aggregate.py from run logs. "
                  f"Total spend across these runs: ${sum(s.usd for s in summaries):.4f}.*"]
    return "\n".join(lines) + "\n"


def gate_lift(off: RunSummary, on: RunSummary) -> str:
    """M1: the headline. The one sentence the project exists to say."""
    if off.is_stub or on.is_stub:
        return "**No M1 figure.** One of these runs is a stub (rules.md §4.1.1)."
    if off.tasks != on.tasks:
        return (f"**No M1 figure.** The arms ran different task counts "
                f"({off.tasks} vs {on.tasks}); the comparison would not be like "
                f"for like.")
    if off.tasks == 0:
        return "**No M1 figure.** No tasks in either arm."

    delta = (on.success_rate or 0) - (off.success_rate or 0)
    cost = f", at {_usd(on)} per solved task" if on.usd_per_solved else ""
    return (
        f"Task success on {on.tasks} SWE-bench Lite tasks went from "
        f"**{_pct(off)}** to **{_pct(on)}** "
        f"({delta:+.0%}) when the eval loop was turned on{cost}."
    )
