"""Load SWE-bench Lite tasks via the official `swebench` package (FR-1).

Build week: 1. Task loading AND grading come from the official package --
never a custom pytest runner (ADR-3, rules.md §2.2). Per-repo environments
are the known project-killer (R1).

Emits: nothing. Pure data access.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from swebench.harness.utils import load_swebench_dataset

SWEBENCH_LITE = "SWE-bench/SWE-bench_Lite"


@dataclass(frozen=True)
class Task:
    """One SWE-bench instance, narrowed to the fields the engine actually uses.

    `gold_patch` is the real human fix shipped with the dataset. The engine
    never shows it to an agent -- it exists so the harness can be verified
    against a known-good input (see verify_harness.py).
    """

    task_id: str
    repo: str
    base_commit: str
    issue: str
    fail_to_pass: list[str]
    pass_to_pass: list[str]
    gold_patch: str
    test_patch: str
    image: str  # the Docker image the harness will run this task in


def _as_list(value: Any) -> list[str]:
    """FAIL_TO_PASS/PASS_TO_PASS arrive as a JSON-encoded string on HF."""
    if isinstance(value, str):
        return list(json.loads(value))
    return list(value or [])


def _to_task(row: dict[str, Any]) -> Task:
    return Task(
        task_id=row["instance_id"],
        repo=row["repo"],
        base_commit=row["base_commit"],
        issue=row["problem_statement"],
        fail_to_pass=_as_list(row.get("FAIL_TO_PASS")),
        pass_to_pass=_as_list(row.get("PASS_TO_PASS")),
        gold_patch=row.get("patch", ""),
        test_patch=row.get("test_patch", ""),
        image=row.get("image", ""),
    )


def load_tasks(
    task_ids: list[str] | None = None,
    dataset: str = SWEBENCH_LITE,
    split: str = "test",
) -> list[Task]:
    """Load tasks by id, or the whole split when `task_ids` is None."""
    rows = load_swebench_dataset(dataset, split, instance_ids=task_ids)
    return [_to_task(dict(row)) for row in rows]


def load_task(
    task_id: str, dataset: str = SWEBENCH_LITE, split: str = "test"
) -> Task:
    """Load exactly one task, or raise KeyError naming the id that missed."""
    tasks = load_tasks([task_id], dataset=dataset, split=split)
    if not tasks:
        raise KeyError(f"{task_id!r} is not in {dataset} split={split}")
    return tasks[0]
