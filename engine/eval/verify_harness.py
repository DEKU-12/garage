"""Verify the Docker grading harness against known inputs -- week-1 exit criterion.

    uv run python -m engine.eval.verify_harness django__django-11099

Two checks, because a grader that cannot fail is worse than no grader:

  1. GOLD patch   -> must return `pass`. The real human fix ships with every
                     SWE-bench task; if it doesn't resolve the task, the
                     harness is wrong and nothing downstream is trustworthy.
  2. EMPTY patch  -> must return `fail`. Proves the verdict tracks the patch
                     and isn't a rubber stamp.

Testing with known-good AND known-bad inputs is the only way to tell a broken
grader apart from a bad model patch later. No model call, no API key, $0.
"""

from __future__ import annotations

import sys
from datetime import datetime, timezone
from pathlib import Path

from engine.errors import GradingInfraError
from engine.eval.grader import grade
from engine.eval.swebench_io import Task, load_task

REPO_ROOT = Path(__file__).resolve().parents[2]


def _check(task: Task, label: str, patch: str, expected: str, work_dir: Path,
           run_id: str) -> bool:
    print(f"\n[{label}] grading ({len(patch.splitlines())} line patch), "
          f"expecting {expected} ...")
    try:
        result = grade(task.task_id, patch, f"{run_id}_{label}", work_dir,
                       image=task.image)
    except GradingInfraError as exc:
        print(f"  INFRA FAILURE: {exc}")
        return False

    ok = result.verdict == expected
    mark = "OK " if ok else "BAD"
    print(f"  {mark} verdict={result.verdict} ({result.wall_ms / 1000:.1f}s)")
    if not ok:
        print(f"  expected {expected}. test output tail:\n{result.log_tail[-1200:]}")
    return ok


def main(argv: list[str]) -> int:
    if len(argv) != 1:
        print(__doc__)
        return 2
    task_id = argv[0]

    print(f"loading {task_id} ...")
    task = load_task(task_id)
    print(f"  repo         {task.repo} @ {task.base_commit[:12]}")
    print(f"  image        {task.image}")
    print(f"  fail_to_pass {len(task.fail_to_pass)} test(s)")
    print(f"  gold patch   {len(task.gold_patch.splitlines())} lines")

    run_id = f"verify_{datetime.now(timezone.utc):%Y%m%d_%H%M%S}"
    work_dir = REPO_ROOT / "runs" / run_id

    gold_ok = _check(task, "gold", task.gold_patch, "pass", work_dir, run_id)
    empty_ok = _check(task, "empty", "", "fail", work_dir, run_id)

    print()
    if gold_ok and empty_ok:
        print("harness verified: gold patch passes, empty patch fails")
        return 0
    print("harness NOT verified -- fix this before trusting any run")
    return 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
