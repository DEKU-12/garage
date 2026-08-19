"""Batch runner: N tasks, resumable, disk-bounded (FR-11).

Build week: 2.

Two constraints shape this file, both measured rather than assumed:

- **Disk.** SWE-bench images are per-TASK, not per-repo, at ~4.2 GB each. Forty
  tasks is ~168 GB. So each task's image is removed once its work is done, and
  peak disk stays around one image instead of the whole set.

- **Time.** ~17 of the 20 minutes a cold task took were spent pulling that
  image. So when an A/B runs both arms, they run BACK TO BACK on the same task
  while its image is warm -- pulling once for two runs instead of twice.

Resumability (FR-11, NFR-4) is not optional at this scale: a 16-hour run WILL
be interrupted. Completed task ids are read back from each arm's results.csv,
so re-running the same command continues rather than restarts.

Emits: run_started, run_finished (week 3).
"""

from __future__ import annotations

import csv
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from engine.agents.stub import StubBackend
from engine.config import RunConfig
from engine.eval.swebench_io import Task, load_task, load_tasks

RESULT_FIELDS = ("task_id", "solved", "attempts", "failure_type",
                 "prompt_tokens", "completion_tokens", "wall_ms", "model")


@dataclass(frozen=True)
class Arm:
    """One side of an A/B: a label and the config change that defines it."""

    label: str
    tester_gate: bool


ARMS = {"on": Arm("on", True), "off": Arm("off", False)}


def completed_task_ids(results_csv: Path) -> set[str]:
    """Task ids already finished in this arm -- the resume set."""
    if not Path(results_csv).is_file():
        return set()
    with Path(results_csv).open(newline="", encoding="utf-8") as handle:
        return {row["task_id"] for row in csv.DictReader(handle) if row.get("task_id")}


def append_result(results_csv: Path, row: dict) -> None:
    """Append one task's row immediately, so a crash loses at most one task."""
    Path(results_csv).parent.mkdir(parents=True, exist_ok=True)
    exists = Path(results_csv).is_file()
    with Path(results_csv).open("a", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=RESULT_FIELDS)
        if not exists:
            writer.writeheader()
        writer.writerow({k: row.get(k, "") for k in RESULT_FIELDS})


def remove_image(image: str, log: Callable[[str], None] = print) -> bool:
    """Drop a task's Docker image. Best effort -- never fails a batch."""
    if not image:
        return False
    proc = subprocess.run(["docker", "rmi", "-f", image],
                          capture_output=True, text=True, check=False)
    if proc.returncode == 0:
        log(f"  pruned image ({image.split(':')[0].split('.')[-1]})")
        return True
    return False


def disk_free_gb() -> float:
    """Free space on the volume Docker stores images on."""
    import shutil
    return shutil.disk_usage("/").free / 1e9


def select_tasks(count: int, repo: str | None = None,
                 dataset: str = "SWE-bench/SWE-bench_Lite") -> list[str]:
    """The first `count` task ids, optionally from one repo.

    Deterministic order, so "the same 30 tasks" means the same 30 tasks in
    every arm and every re-run -- which is the whole basis of the A/B (NFR-1).
    """
    rows = load_tasks(dataset=dataset)
    ids = [r.task_id for r in rows if repo is None or r.repo == repo]
    return sorted(ids)[:count]
