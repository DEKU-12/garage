"""CLI entrypoints. Build week: 1 (run-one); 2 adds run-batch and report.

    uv run python -m engine.cli run-one --task django__django-11099 --model stub

run-one walks one task through the week-1 loop: build a patch, apply it to a
clean worktree, grade it in Docker, and on failure hand the reason back to the
builder and try again. Every attempt's prompt, response, patch, and test output
is persisted (FR-6) so any run is answerable after the fact.

Emits: run_started, run_finished (once events.py lands in week 3).
"""

from __future__ import annotations

import argparse
import csv
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

from engine.agents.builder import build_patch
from engine.agents.stub import (
    EMPTY,
    MALFORMED_DIFF,
    PROSE_ONLY,
    StubBackend,
    builder_script,
)
from engine.config import STUB_MODEL, RunConfig, prompt_hashes
from engine.errors import GradingInfraError, ModelCallError, PatchError, WorkspaceError
from engine.eval.grader import grade
from engine.eval.swebench_io import Task, load_task
from engine.repo.patch import apply_patch, extract_diff, tree_diff, validate_diff
from engine.repo.workspace import attempt_worktree

REPO_ROOT = Path(__file__).resolve().parents[1]
PROMPTS = REPO_ROOT / "engine" / "prompts"

RESULT_FIELDS = [
    "task_id", "solved", "attempts", "failure_type",
    "prompt_tokens", "completion_tokens", "wall_ms", "model",
]


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)


def _attempt_dir(run_dir: Path, task_id: str, n: int) -> Path:
    return run_dir / "tasks" / task_id / "attempts" / str(n)


def run_one(task: Task, cfg: RunConfig, run_dir: Path, stub: StubBackend | None,
            max_attempts: int, context: str = "") -> dict[str, object]:
    """One task, up to `max_attempts` builder attempts. Returns its results row.

    Never raises for a model or patch failure -- those are outcomes, recorded
    and returned. Only a genuinely broken environment escapes (rules.md §3.1).
    """
    started = time.monotonic()
    prompt_tokens = completion_tokens = 0
    failure_type = ""
    feedback: str | None = None
    solved = False
    attempt = 0

    for attempt in range(1, max_attempts + 1):
        adir = _attempt_dir(run_dir, task.task_id, attempt)
        print(f"\n--- attempt {attempt}/{max_attempts} ---")

        try:
            raw, user_msg, usage = build_patch(task.issue, context, cfg, stub, feedback)
        except ModelCallError as exc:
            print(f"  model call failed: {exc}")
            failure_type = "model_error"
            break
        prompt_tokens += usage.prompt_tokens
        completion_tokens += usage.completion_tokens
        _write(adir / "prompt_builder.md", user_msg)
        _write(adir / "response.md", raw)
        print(f"  builder responded ({len(raw)} chars, {usage.latency_ms}ms)")

        with attempt_worktree(task.repo, task.base_commit, task.task_id,
                              attempt, REPO_ROOT / "workspaces") as tree:
            try:
                diff = extract_diff(raw)
                validate_diff(diff, tree)
            except PatchError as exc:
                print(f"  patch rejected: {exc}")
                failure_type = "patch_apply_error"
                feedback = str(exc)
                continue

            applied = apply_patch(diff, tree)
            if not applied.applied:
                print(f"  git apply failed: {applied.stderr.splitlines()[:1]}")
                failure_type = "patch_apply_error"
                feedback = applied.stderr  # git's own words, verbatim
                continue

            submission = tree_diff(tree)
            _write(adir / "patch.diff", submission)
            print(f"  applied ({applied.mode}), {len(submission.splitlines())} line patch")

        try:
            result = grade(task.task_id, submission, f"{cfg.run_id}_a{attempt}",
                           run_dir, image=task.image)
        except GradingInfraError as exc:
            print(f"  GRADING INFRA FAILURE: {exc}")
            failure_type = "crashed"  # never blamed on the model (rules.md §3.1)
            break

        _write(adir / "test_output.txt", result.log_tail)
        print(f"  tests: {result.verdict} ({result.wall_ms / 1000:.1f}s)")

        if result.resolved:
            solved = True
            failure_type = ""
            break

        failure_type = "failed_tests"
        feedback = (
            "The patch applied cleanly but the tests still fail:\n\n"
            f"{result.log_tail[-2000:]}"
        )

    return {
        "task_id": task.task_id,
        "solved": solved,
        "attempts": attempt,
        "failure_type": failure_type,
        "prompt_tokens": prompt_tokens,
        "completion_tokens": completion_tokens,
        "wall_ms": int((time.monotonic() - started) * 1000),
        "model": cfg.model_for("builder"),
    }


def cmd_run_one(args: argparse.Namespace) -> int:
    task = load_task(args.task)
    run_id = args.run_id or f"r_{datetime.now(timezone.utc):%Y%m%d_%H%M%S}"
    run_dir = REPO_ROOT / "runs" / run_id

    cfg = RunConfig(
        run_id=run_id,
        task_ids=[task.task_id],
        model_for_role={r: args.model for r in
                        ("orchestrator", "scout", "builder", "tester", "reviewer", "scribe")},
        prompt_hashes=prompt_hashes(PROMPTS),
    )
    cfg.freeze_to(run_dir / "config.json")

    stub = None
    if cfg.is_stub_run:
        print("=" * 68)
        print("STUB RUN -- canned responses, no model called. NOT A RESULT.")
        print("Nothing from this run may appear in a report (rules.md §4.1.1).")
        print("=" * 68)
        canned = {"prose": PROSE_ONLY, "malformed": MALFORMED_DIFF, "empty": EMPTY}
        before = [canned[name] for name in args.stub_failures.split(",") if name]
        if before:
            print(f"scripted failures before the fix: {args.stub_failures}")
        stub = StubBackend(
            scripts={"builder": builder_script(task.gold_patch, before=before)}
        )

    print(f"\ntask {task.task_id}  ({task.repo} @ {task.base_commit[:12]})")
    print(f"run  {run_id}  model={args.model}")

    max_attempts = args.max_attempts or (cfg.max_correctness_retries + 1)
    try:
        row = run_one(task, cfg, run_dir, stub, max_attempts)
    except WorkspaceError as exc:
        print(f"\nWORKSPACE FAILURE: {exc}")
        return 3

    results = run_dir / "results.csv"
    with results.open("w", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=RESULT_FIELDS)
        writer.writeheader()
        writer.writerow(row)

    print("\n" + "=" * 68)
    print(f"{'SOLVED' if row['solved'] else 'NOT SOLVED'}  "
          f"attempts={row['attempts']}  failure={row['failure_type'] or '-'}  "
          f"{row['wall_ms'] / 1000:.1f}s")
    print(f"artifacts: runs/{run_id}/")
    return 0 if row["solved"] else 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="garage")
    sub = parser.add_subparsers(dest="cmd", required=True)

    one = sub.add_parser("run-one", help="run a single SWE-bench task end to end")
    one.add_argument("--task", required=True, help="e.g. django__django-11099")
    one.add_argument("--model", default=STUB_MODEL,
                     help=f"model for every role (default: {STUB_MODEL})")
    one.add_argument("--max-attempts", type=int, default=None,
                     help="default: max_correctness_retries + 1")
    one.add_argument("--run-id", default=None)
    one.add_argument("--stub-failures", default="",
                     help="stub only: comma list of failures to inject before "
                          "the fix, from prose,malformed,empty -- exercises the "
                          "retry loop offline")
    one.set_defaults(func=cmd_run_one)

    args = parser.parse_args(argv)
    return int(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main())
