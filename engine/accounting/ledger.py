"""One CSV row per model call: role, model, tokens, dollars, latency (FR-32).

Build week: 2.

The ledger is the raw material for M2 ($/solved). It is deliberately
append-only and one-row-per-call rather than a running total: a total can only
answer "how much", while rows can answer "which role, which attempt, which
model" -- and the bake-off (E4, M6) is exactly that question.

    $/solved = sum(ledger.usd) / count(results.solved)

computed in report/aggregate.py from these rows. Never estimated, never
accumulated in a variable that a crash could lose.

Emits: cost_tick (week 3).
"""

from __future__ import annotations

import csv
from dataclasses import dataclass
from pathlib import Path

from engine.accounting.pricing import cost_usd, is_priced

FIELDS = (
    "task_id", "attempt", "role", "model",
    "prompt_tokens", "completion_tokens", "usd", "priced", "latency_ms",
)


@dataclass(frozen=True)
class LedgerRow:
    task_id: str
    attempt: int
    role: str
    model: str
    prompt_tokens: int
    completion_tokens: int
    usd: float
    priced: bool
    latency_ms: int


def row_for(task_id: str, attempt: int, role: str, model: str,
            prompt_tokens: int, completion_tokens: int,
            latency_ms: int) -> LedgerRow:
    """Price one call. `priced` records whether the figure is real."""
    return LedgerRow(
        task_id=task_id,
        attempt=attempt,
        role=role,
        model=model,
        prompt_tokens=prompt_tokens,
        completion_tokens=completion_tokens,
        usd=cost_usd(model, prompt_tokens, completion_tokens),
        priced=is_priced(model),
        latency_ms=latency_ms,
    )


def append(path: Path, rows: list[LedgerRow]) -> None:
    """Append rows, writing the header once. Safe to call repeatedly."""
    if not rows:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    exists = path.is_file()
    with path.open("a", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=FIELDS)
        if not exists:
            writer.writeheader()
        for row in rows:
            writer.writerow({
                "task_id": row.task_id, "attempt": row.attempt, "role": row.role,
                "model": row.model, "prompt_tokens": row.prompt_tokens,
                "completion_tokens": row.completion_tokens,
                "usd": f"{row.usd:.6f}", "priced": row.priced,
                "latency_ms": row.latency_ms,
            })


def read(path: Path) -> list[LedgerRow]:
    """Read a ledger back. Empty when the file does not exist."""
    if not Path(path).is_file():
        return []
    with Path(path).open(newline="", encoding="utf-8") as handle:
        return [
            LedgerRow(
                task_id=r["task_id"], attempt=int(r["attempt"]), role=r["role"],
                model=r["model"], prompt_tokens=int(r["prompt_tokens"]),
                completion_tokens=int(r["completion_tokens"]),
                usd=float(r["usd"]), priced=r["priced"] == "True",
                latency_ms=int(r["latency_ms"]),
            )
            for r in csv.DictReader(handle)
        ]
