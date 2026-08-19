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
import json
import time
from datetime import datetime, timezone
from pathlib import Path

from dotenv import load_dotenv

from engine.batch import (
    ARMS,
    append_result,
    completed_task_ids,
    disk_free_gb,
    remove_image,
    select_tasks,
)
from engine.errors import QuotaExhausted
from engine.graph import build_graph
from engine.report.aggregate import gate_lift, render, summarize
from engine.state import TaskState, new_state
from engine.agents.stub import (
    REJECT_RUNG_3,
    reviewer_script,
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
from engine.repo.patch import (
    apply_patch,
    extract_diff,
    repair_hunk_headers,
    tree_diff,
    validate_diff,
)
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
            max_attempts: int | None = None, context: str = "") -> dict[str, object]:
    """One task through the state machine. Returns its results row.

    Never raises for a model or patch failure -- those are outcomes, recorded
    and returned. Only a genuinely broken environment escapes (rules.md §3.1).

    Routing, retry caps and gate wiring all live in engine/graph.py; this
    function only starts the machine and writes down what came out.
    """
    started = time.monotonic()

    state = new_state(
        task_id=task.task_id,
        issue=task.issue,
        repo=task.repo,
        base_commit=task.base_commit,
        fail_to_pass=list(task.fail_to_pass),
        image=task.image,
    )
    if context:
        state["context"] = context

    if not cfg.scout and not context:
        print("  scout: OFF (builder sees the issue only)")

    machine = build_graph(cfg, run_dir, stub)
    # The graph's own recursion guard. Each builder run costs at most four node
    # visits (builder, tester, reviewer, routing), plus scout and the terminal
    # node -- generous headroom over the 1+3+1 termination proof.
    final: TaskState = machine.invoke(
        state, {"recursion_limit": 4 * cfg.max_builder_runs + 10}
    )

    # FR-6: per-attempt timing and token counts, written from the final state
    # so an attempt can never finish without leaving a record.
    for attempt in final.get("attempts", []):
        usage = attempt.get("usage") or {}
        meta = {
            "attempt": attempt.get("n"),
            "model": cfg.model_for("builder"),
            "outcome": attempt.get("failure") or (
                "solved" if attempt.get("test_verdict") == "pass" else "failed_tests"
            ),
            "patch_applied": attempt.get("patch_applied", False),
            "apply_mode": attempt.get("apply_mode", ""),
            "test_verdict": attempt.get("test_verdict"),
            "review_verdict": attempt.get("review_verdict"),
            "review_reason": attempt.get("review_reason"),
            "wall_ms": attempt.get("wall_ms", 0),
            "error": attempt.get("error", ""),
            **{k: usage.get(k) for k in
               ("prompt_tokens", "completion_tokens", "latency_ms", "finish_reason")},
        }
        _write(_attempt_dir(run_dir, task.task_id, attempt["n"]) / "meta.json",
               json.dumps(meta, indent=2, sort_keys=True))

    return {
        "task_id": task.task_id,
        "solved": final.get("status") == "shipped",
        "attempts": len(final.get("attempts", [])),
        "failure_type": final.get("failure_type", ""),
        "prompt_tokens": final.get("prompt_tokens", 0),
        "completion_tokens": final.get("completion_tokens", 0),
        "wall_ms": int((time.monotonic() - started) * 1000),
        "model": cfg.model_for("builder"),
    }


def _stub_for(task: Task, args: argparse.Namespace) -> StubBackend:
    """Canned responses for one task, scripted by the CLI flags."""
    canned = {"prose": PROSE_ONLY, "malformed": MALFORMED_DIFF, "empty": EMPTY}
    failures = getattr(args, "stub_failures", "") or ""
    before = [canned[name] for name in failures.split(",") if name]
    reviewer_before = [REJECT_RUNG_3] if getattr(args, "stub_reject", False) else []
    return StubBackend(
        scripts={
            "builder": builder_script(task.gold_patch, before=before),
            # Without its own script the reviewer would be handed the builder's
            # cursor and reply with a diff -- parsed as ACCEPT with a warning.
            # Correct, but it never exercises the gate.
            "reviewer": reviewer_script(before=reviewer_before),
        }
    )


def cmd_run_one(args: argparse.Namespace) -> int:
    task = load_task(args.task)
    run_id = args.run_id or f"r_{datetime.now(timezone.utc):%Y%m%d_%H%M%S}"
    run_dir = REPO_ROOT / "runs" / run_id

    cfg = RunConfig(
        run_id=run_id,
        task_ids=[task.task_id],
        model_for_role={r: args.model for r in
                        ("orchestrator", "scout", "builder", "tester", "reviewer", "scribe")},
        scout=not args.no_scout,
        prompt_hashes=prompt_hashes(PROMPTS),
    )
    cfg.freeze_to(run_dir / "config.json")

    stub = None
    if cfg.is_stub_run:
        print("=" * 68)
        print("STUB RUN -- canned responses, no model called. NOT A RESULT.")
        print("Nothing from this run may appear in a report (rules.md §4.1.1).")
        print("=" * 68)
        stub = _stub_for(task, args)

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


def cmd_run_batch(args: argparse.Namespace) -> int:
    """N tasks x one or two gate arms, resumable, one image on disk at a time."""
    arms = [ARMS[a] for a in args.arms.split(",") if a]
    task_ids = select_tasks(args.tasks, repo=args.repo or None)
    if not task_ids:
        print("no tasks matched")
        return 2

    base = args.run_id or f"b_{datetime.now(timezone.utc):%Y%m%d_%H%M%S}"
    run_dirs = {arm.label: REPO_ROOT / "runs" / f"{base}_{arm.label}" for arm in arms}
    done = {arm.label: completed_task_ids(run_dirs[arm.label] / "results.csv")
            for arm in arms}

    print(f"batch {base}: {len(task_ids)} task(s) x {len(arms)} arm(s) "
          f"[{', '.join(a.label for a in arms)}]  model={args.model}")
    already = sum(len(v) for v in done.values())
    if already:
        print(f"resuming: {already} task-arm(s) already complete, skipping them")
    print(f"disk free: {disk_free_gb():.1f} GB")

    started = time.monotonic()
    completed = 0
    for i, task_id in enumerate(task_ids, 1):
        if all(task_id in done[arm.label] for arm in arms):
            print(f"\n[{i}/{len(task_ids)}] {task_id} -- done already, skipping")
            continue

        task = load_task(task_id)
        print(f"\n{'=' * 68}\n[{i}/{len(task_ids)}] {task_id}")

        for arm in arms:
            if task_id in done[arm.label]:
                print(f"  arm {arm.label}: already done")
                continue
            # Both arms run while the image is warm: ~17 of a cold task's 20
            # minutes was the pull, so pulling once for two runs halves it.
            print(f"\n--- arm: tester gate {arm.label.upper()} ---")
            run_dir = run_dirs[arm.label]
            cfg = RunConfig(
                run_id=f"{base}_{arm.label}",
                task_ids=[task_id],
                model_for_role={r: args.model for r in
                                ("orchestrator", "scout", "builder", "tester",
                                 "reviewer", "scribe")},
                tester_gate=arm.tester_gate,
                scout=not args.no_scout,
                prompt_hashes=prompt_hashes(PROMPTS),
            )
            cfg.freeze_to(run_dir / "config.json")
            stub = _stub_for(task, args) if cfg.is_stub_run else None
            try:
                row = run_one(task, cfg, run_dir, stub)
            except QuotaExhausted as exc:
                # Stop the batch rather than grinding the rest of the task list
                # into identical failures. Nothing is written for this task, so
                # re-running the same --run-id resumes exactly here.
                print(f"\n{'=' * 68}")
                print(f"QUOTA EXHAUSTED -- stopping the batch.\n  {exc}")
                print(f"\n{completed} task-arm run(s) recorded before the wall.")
                print("Nothing was recorded for this task, so re-running the "
                      "same command resumes from here once the quota resets:")
                print(f"  uv run python -m engine.cli run-batch --tasks "
                      f"{args.tasks} --repo {args.repo} --model {args.model} "
                      f"--arms {args.arms} --run-id {base}")
                if not args.keep_images:
                    remove_image(task.image)
                return 3
            append_result(run_dir / "results.csv", row)
            completed += 1
            print(f"  -> solved={row['solved']} attempts={row['attempts']} "
                  f"{row['failure_type'] or ''}")

        if not args.keep_images:
            remove_image(task.image)
        print(f"  disk free: {disk_free_gb():.1f} GB  "
              f"elapsed: {(time.monotonic() - started) / 60:.1f} min")

    mins = (time.monotonic() - started) / 60
    print(f"\n{'=' * 68}")
    print(f"batch done: {completed} task-arm run(s) in {mins:.1f} min"
          + (f" ({mins / completed:.1f} min each)" if completed else ""))
    for arm in arms:
        print(f"  {arm.label}: {run_dirs[arm.label]}")
    print(f"\nreport with:  uv run python -m engine.cli report "
          + " ".join(f"runs/{base}_{a.label}" for a in arms))
    return 0


def cmd_report(args: argparse.Namespace) -> int:
    """Turn run logs into the tables. The only source of reported numbers."""
    summaries = [summarize(Path(d)) for d in args.run_dirs]
    print(render(summaries, title=args.title))
    if len(summaries) == 2:
        off = next((s for s in summaries if not s.gates.get("tester_gate")), None)
        on = next((s for s in summaries if s.gates.get("tester_gate")), None)
        if off and on:
            print("## M1 -- gate lift\n")
            print(gate_lift(off, on))
    if args.out:
        Path(args.out).write_text(render(summaries, title=args.title), encoding="utf-8")
        print(f"\nwritten to {args.out}")
    return 0


def main(argv: list[str] | None = None) -> int:
    # rules.md §1: .env at project root, override=True. A key already in
    # the environment still works -- load_dotenv is a no-op without a file.
    load_dotenv(REPO_ROOT / ".env", override=True)
    parser = argparse.ArgumentParser(prog="garage")
    sub = parser.add_subparsers(dest="cmd", required=True)

    one = sub.add_parser("run-one", help="run a single SWE-bench task end to end")
    one.add_argument("--task", required=True, help="e.g. django__django-11099")
    one.add_argument("--model", default=STUB_MODEL,
                     help=f"model for every role (default: {STUB_MODEL})")
    one.add_argument("--max-attempts", type=int, default=None,
                     help="default: max_correctness_retries + 1")
    one.add_argument("--run-id", default=None)
    one.add_argument("--stub-reject", action="store_true",
                     help="stub only: reviewer rejects once before accepting")
    one.add_argument("--no-scout", action="store_true",
                     help="builder sees the issue only -- the OFF arm of E2 (FR-10)")
    one.add_argument("--stub-failures", default="",
                     help="stub only: comma list of failures to inject before "
                          "the fix, from prose,malformed,empty -- exercises the "
                          "retry loop offline")
    one.set_defaults(func=cmd_run_one)

    batch = sub.add_parser("run-batch",
                           help="N tasks x gate arms, resumable, disk-bounded")
    batch.add_argument("--tasks", type=int, default=5,
                       help="how many tasks (deterministic order)")
    batch.add_argument("--repo", default="django/django",
                       help="restrict to one repo; '' for all of SWE-bench Lite")
    batch.add_argument("--model", default=STUB_MODEL)
    batch.add_argument("--arms", default="on,off",
                       help="tester-gate arms to run: on, off, or on,off (FR-10)")
    batch.add_argument("--run-id", default=None,
                       help="reuse the same id to RESUME an interrupted batch")
    batch.add_argument("--no-scout", action="store_true")
    batch.add_argument("--keep-images", action="store_true",
                       help="skip pruning -- needs ~4.2 GB of disk per task")
    batch.add_argument("--stub-failures", default="")
    batch.add_argument("--stub-reject", action="store_true")
    batch.set_defaults(func=cmd_run_batch)

    rep = sub.add_parser("report", help="turn run logs into the M1-M3 tables")
    rep.add_argument("run_dirs", nargs="+", help="one or more runs/<id> paths")
    rep.add_argument("--title", default="Results")
    rep.add_argument("--out", default=None, help="also write the markdown here")
    rep.set_defaults(func=cmd_report)

    args = parser.parse_args(argv)
    return int(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main())
