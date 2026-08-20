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
    RESULT_FIELDS,
    append_result,
    completed_task_ids,
    disk_free_gb,
    remove_image,
    result_row,
    resume_conflict,
    run_spend_usd,
    select_tasks,
)
from dataclasses import replace

from engine.errors import QuotaExhausted
# qualified: engine.report.aggregate also exports render(), and a bare
# import of both silently shadows one of them
from engine import approval, preflight as pf
from engine.events import EventLog
from engine.graph import build_graph
from engine.report.aggregate import common_tasks, gate_lift, render, summarize
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



def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)


def _attempt_dir(run_dir: Path, task_id: str, n: int) -> Path:
    return run_dir / "tasks" / task_id / "attempts" / str(n)


def run_one(task: Task, cfg: RunConfig, run_dir: Path, stub: StubBackend | None,
            max_attempts: int | None = None, context: str = "",
            events: EventLog | None = None, grader=None) -> dict[str, object]:
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

    if events is not None:
        events.emit("task_started", agent="orchestrator", task_id=task.task_id,
                    repo=task.repo, base_commit=task.base_commit,
                    fail_to_pass=len(task.fail_to_pass))

    machine = build_graph(cfg, run_dir, stub, events=events, grader=grader)
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
        "status": final.get("status", ""),
        # what proved it, for the approval prompt and the PR body
        "witness_tests": (final.get("attempts") or [{}])[-1].get("witness_tests", []),
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
        reviewer_gate=not args.no_reviewer_gate,
        prompt_hashes=prompt_hashes(PROMPTS),
    )
    cfg.freeze_to(run_dir / "config.json")

    events = EventLog.for_run(run_dir, run_id)
    events.emit("run_started", agent="orchestrator",
                model=args.model, tasks=1,
                gates={"tester": cfg.tester_gate, "reviewer": cfg.reviewer_gate,
                       "scout": cfg.scout})

    if not cfg.is_stub_run and not args.skip_preflight:
        # Before the 300MB clone and the Docker pull, not after: a typo in a
        # key should cost two seconds, not three minutes.
        checks = pf.preflight(args.model, want_pr=getattr(args, "pr", False))
        bad = pf.blocking(checks)
        if bad:
            print("\ncannot start this run:\n")
            print(pf.render(checks))
            print("\nFix the above, or run offline with --model stub.")
            return 4

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
        row = run_one(task, cfg, run_dir, stub, max_attempts, events=events)
    except WorkspaceError as exc:
        print(f"\nWORKSPACE FAILURE: {exc}")
        return 3

    results = run_dir / "results.csv"
    with results.open("w", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=RESULT_FIELDS)
        writer.writeheader()
        writer.writerow(result_row(row))

    print("\n" + "=" * 68)
    print(f"{'SOLVED' if row['solved'] else 'NOT SOLVED'}  "
          f"attempts={row['attempts']}  failure={row['failure_type'] or '-'}  "
          f"{row['wall_ms'] / 1000:.1f}s")
    events.emit("run_finished", agent="orchestrator", task_arms=1)
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
    # Refuse to resume a run built under different settings. Without this a
    # case-insensitive filesystem is enough to silently inherit another
    # experiment's rows -- which is how a Groq run once got reported under a
    # Sonnet heading.
    for arm in arms:
        probe = RunConfig(
            run_id=f"{base}_{arm.label}", task_ids=task_ids,
            model_for_role={r: args.model for r in
                            ("orchestrator", "scout", "builder", "tester",
                             "reviewer", "scribe")},
            tester_gate=arm.tester_gate,
            reviewer_gate=not args.no_reviewer_gate,
            scout=not args.no_scout,
        )
        conflict = resume_conflict(run_dirs[arm.label], probe)
        if conflict:
            print(f"REFUSING TO RESUME\n  {conflict}")
            return 2

    done = {arm.label: completed_task_ids(run_dirs[arm.label] / "results.csv")
            for arm in arms}

    print(f"batch {base}: {len(task_ids)} task(s) x {len(arms)} arm(s) "
          f"[{', '.join(a.label for a in arms)}]  model={args.model}")
    already = sum(len(v) for v in done.values())
    if already:
        print(f"resuming: {already} task-arm(s) already complete, skipping them")
    print(f"disk free: {disk_free_gb():.1f} GB")

    arm_logs = {
        arm.label: EventLog.for_run(run_dirs[arm.label], f"{base}_{arm.label}")
        for arm in arms
    }
    for arm in arms:
        arm_logs[arm.label].emit(
            "run_started", agent="orchestrator", model=args.model,
            tasks=len(task_ids),
            gates={"tester": arm.tester_gate,
                   "reviewer": not args.no_reviewer_gate,
                   "scout": not args.no_scout},
        )

    started = time.monotonic()
    completed = 0
    for i, task_id in enumerate(task_ids, 1):
        if all(task_id in done[arm.label] for arm in arms):
            print(f"\n[{i}/{len(task_ids)}] {task_id} -- done already, skipping")
            continue

        spent = run_spend_usd(list(run_dirs.values()))
        if args.max_usd and spent >= args.max_usd:
            print(f"\n{'=' * 68}")
            print(f"BUDGET REACHED -- ${spent:.4f} of ${args.max_usd:.2f}. Stopping.")
            print(f"{completed} task-arm run(s) this session. Re-run the same "
                  f"--run-id with a higher --max-usd to continue.")
            return 4

        task = load_task(task_id)
        print(f"\n{'=' * 68}\n[{i}/{len(task_ids)}] {task_id}  "
              f"(spent ${spent:.4f})")

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
                reviewer_gate=not args.no_reviewer_gate,
                scout=not args.no_scout,
                prompt_hashes=prompt_hashes(PROMPTS),
            )
            cfg.freeze_to(run_dir / "config.json")
            arm_events = arm_logs[arm.label]
            stub = _stub_for(task, args) if cfg.is_stub_run else None
            try:
                row = run_one(task, cfg, run_dir, stub, events=arm_events)
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
    for arm in arms:
        arm_logs[arm.label].emit("run_finished", agent="orchestrator",
                                 task_arms=completed, wall_min=round(mins, 1))
    print(f"\n{'=' * 68}")
    print(f"batch done: {completed} task-arm run(s) in {mins:.1f} min"
          + (f" ({mins / completed:.1f} min each)" if completed else ""))
    for arm in arms:
        print(f"  {arm.label}: {run_dirs[arm.label]}")
    print(f"\nreport with:  uv run python -m engine.cli report "
          + " ".join(f"runs/{base}_{a.label}" for a in arms))
    return 0


WITNESS_CLAUSE = """

--- HOW THIS WILL BE JUDGED (read before writing any code) ---

This repository ships no list of tests that must pass, so a patch that merely
applies proves nothing. Your change is accepted only if BOTH hold:

  1. No test that passes today starts failing.
  2. You add or change a test that FAILS on the current code and PASSES with
     your fix. This is the witness test, and without one the result is
     reported as UNVERIFIED rather than as a fix.

So your diff must contain two things: the fix, and a test that demonstrates it.
Put the test where this project already keeps its tests.
"""


def cmd_doctor(args: argparse.Namespace) -> int:
    """Can this machine do a real run? Answer before it costs anything."""
    print(f"\nchecking for a run with model={args.model}\n")
    checks = pf.preflight(args.model, want_pr=args.pr,
                          need_docker=not args.no_docker, live=not args.offline)
    print(pf.render(checks))
    bad = pf.blocking(checks)
    print()
    if bad:
        print(f"{len(bad)} thing(s) to fix before a real run. "
              "Everything works offline right now with --model stub.")
        return 1
    print("ready. Nothing above spent more than a single token.")
    return 0


def _repo_stub(args: argparse.Namespace) -> StubBackend:
    """Canned responses for repo mode.

    There is deliberately no correct answer here. The benchmark stub replays
    `task.gold_patch` -- the known human fix that ships with the dataset -- and
    an arbitrary repo has no equivalent, so inventing one would mean testing
    the pipeline against a fiction.

    So this stub only ever produces responses that fail to apply. That still
    exercises everything worth exercising offline: the builder loop, the retry
    accounting, the caps and the event log. It can never reach the grader,
    which is honest -- offline, there is nothing to grade.
    """
    canned = {"prose": PROSE_ONLY, "malformed": MALFORMED_DIFF, "empty": EMPTY}
    names = (getattr(args, "stub_failures", "") or "prose").split(",")
    script = [canned[n] for n in names if n in canned] or [PROSE_ONLY]
    reviewer_before = [REJECT_RUNG_3] if getattr(args, "stub_reject", False) else []
    return StubBackend(scripts={"builder": script * 6,
                                "reviewer": reviewer_script(before=reviewer_before)})


def _preflight_or_stop(args: argparse.Namespace, *, need_docker: bool = True) -> int:
    """0 to carry on. Runs before any clone, pull or model call."""
    if args.model == STUB_MODEL or getattr(args, "skip_preflight", False):
        return 0
    checks = pf.preflight(args.model, want_pr=getattr(args, "pr", False),
                          need_docker=need_docker)
    bad = pf.blocking(checks)
    if not bad:
        return 0
    print("\ncannot start this run:\n")
    print(pf.render(checks))
    print("\nFix the above, or run offline with --model stub.")
    return 4


def cmd_run_repo(args: argparse.Namespace) -> int:
    """The repo front door: point at a GitHub URL instead of a benchmark id."""
    if (stop := _preflight_or_stop(args)):
        return stop

    from engine.eval.repo_grader import attempt_grader
    from engine.graph import WORKSPACES
    from engine.repo import ship as shipping
    from engine.repo.front_door import open_repo

    run_id = args.run_id or f"repo_{datetime.now(timezone.utc):%Y%m%d_%H%M%S}"
    run_dir = REPO_ROOT / "runs" / run_id

    try:
        task = open_repo(args.url, args.issue + WITNESS_CLAUSE, WORKSPACES)
    except WorkspaceError as exc:
        print(f"\nCANNOT OPEN REPO: {exc}")
        return 3

    print(f"\nrepo  {task.repo} @ {task.base_commit[:12]} ({task.default_branch})")
    print(f"suite {task.suite.kind} -- {task.suite.why}")
    if not task.suite.per_test:
        print("      NOTE: this suite cannot name individual tests, so the best")
        print("      outcome available is UNVERIFIED, never a confirmed fix.")
    print(f"run   {run_id}  model={args.model}")

    cfg = RunConfig(
        run_id=run_id,
        task_ids=[task.task_id],
        model_for_role={r: args.model for r in
                        ("orchestrator", "scout", "builder", "tester", "reviewer", "scribe")},
        scout=not args.no_scout,
        reviewer_gate=not args.no_reviewer_gate,
        prompt_hashes=prompt_hashes(PROMPTS),
    )
    cfg.freeze_to(run_dir / "config.json")

    events = EventLog.for_run(run_dir, run_id)
    events.emit("run_started", agent="orchestrator", model=args.model, tasks=1,
                gates={"tester": cfg.tester_gate, "reviewer": cfg.reviewer_gate,
                       "scout": cfg.scout})

    stub = None
    if cfg.is_stub_run:
        print("=" * 68)
        print("STUB RUN -- canned responses, no model called. NOT A RESULT.")
        print("In repo mode the stub cannot produce a real fix: the benchmark")
        print("stub replays the task's known human patch, and your repo has no")
        print("such thing. This exercises the plumbing and always ends in")
        print("patch_apply_error. Use a real model to actually repair anything.")
        print("=" * 68)
        stub = _repo_stub(args)

    # Grading is minutes of silence otherwise: `tests_run` fires once and the
    # next event is the verdict. Seventeen quiet minutes is indistinguishable
    # from a hang, so each phase announces itself.
    def phase(name: str) -> None:
        events.emit("tests_run", agent="tester", task_id=task.task_id, phase=name)

    grader = attempt_grader(task, WORKSPACES, progress=phase)
    try:
        row = run_one(task, cfg, run_dir, stub, args.max_attempts,
                      events=events, grader=grader)
    except WorkspaceError as exc:
        print(f"\nWORKSPACE FAILURE: {exc}")
        return 3
    finally:
        # The prepared image is a cache, not an artifact: it must not outlive
        # the run that built it.
        grader.close()

    status = str(row.get("status", ""))
    verdict = "pass" if status == "shipped" else (
        "unverified" if status == "unverified" else "fail")

    print("\n" + "=" * 68)
    headline = {"shipped": "FIXED", "unverified": "UNVERIFIED"}.get(status, "NOT FIXED")
    print(f"{headline}  attempts={row['attempts']}  "
          f"failure={row['failure_type'] or '-'}  {row['wall_ms'] / 1000:.1f}s")
    if status == "unverified":
        print("  The suite is still green, but nothing here demonstrates a fix.")
        print("  This is NOT reported as solved (rules.md §0).")

    # Repo mode wrote events and a ledger but no results row, so `report` could
    # not read a repo run at all and there was no table across repairs.
    results = run_dir / "results.csv"
    with results.open("w", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=RESULT_FIELDS)
        writer.writeheader()
        writer.writerow(result_row(row))

    if args.branch or args.push or args.pr:
        if verdict == "fail":
            print("\nno branch: nothing to ship for a failed run")
        else:
            _ship(task, run_dir, verdict, row, args, shipping, events=events)

    events.emit("run_finished", agent="orchestrator", task_arms=1)
    print(f"artifacts: runs/{run_id}/")
    return 0 if status == "shipped" else (2 if status == "unverified" else 1)


def _final_patch(run_dir: Path, task_id: str) -> tuple[str, int]:
    """The last patch that actually applied, straight off disk.

    Read from the artifacts rather than carried in memory, so what gets
    committed is provably the same bytes the tester graded.
    """
    base = run_dir / "tasks" / task_id / "attempts"
    for n in sorted((int(d.name) for d in base.glob("*") if d.name.isdigit()),
                    reverse=True):
        diff = base / str(n) / "patch.diff"
        if diff.is_file() and diff.read_text().strip():
            return diff.read_text(), n
    return "", 0


def _ship(task, run_dir: Path, verdict: str, row: dict, args, shipping,
          events=None) -> None:
    """Branch, commit, and -- only if explicitly asked -- push and open a PR.

    Every one of those three steps asks a human first (engine/approval.py).
    The flags say what you are WILLING to do; the prompts are where you
    actually decide, having seen the diff. A run that finishes at 3am with
    nobody watching writes nothing.
    """
    from engine.graph import WORKSPACES
    from engine.repo.patch import apply_patch
    from engine.repo.workspace import attempt_worktree

    patch, attempt_n = _final_patch(run_dir, task.task_id)
    if not patch:
        print("\nno branch: no applied patch on disk to commit")
        return

    branch = shipping.branch_name(task.task_id, verdict)
    title = ("fix: " if verdict == "pass" else "unverified: ") + args.issue.splitlines()[0][:72]
    witness = list(row.get("witness_tests") or [])
    body = shipping.pr_body(task, verdict, str(row.get("failure_type") or ""),
                            witness, [], int(row.get("attempts", 0)))
    assume = getattr(args, "yes", False)

    def gate(action: str) -> bool:
        """Show the change, ask, and put the answer in the log."""
        print(approval.describe(action, repo=task.repo, branch=branch,
                                base=task.default_branch, verdict=verdict,
                                patch=patch, witness=witness,
                                attempts=int(row.get("attempts", 0))))
        d = approval.ask(action, verdict=verdict, assume_yes=assume)
        if events is not None:
            # A human decision is a gate in exactly the sense the frozen v:1
            # schema already means, so it needs no new event type (ADR-10) and
            # lands on the replay tape like any other verdict.
            events.emit("gate_verdict", agent="orchestrator", task_id=task.task_id,
                        gate="human", verdict="approve" if d.approved else "decline",
                        action=action, how=d.how, branch=branch)
        return d.approved

    with attempt_worktree(task.repo, task.base_commit, task.task_id,
                          9000 + attempt_n, WORKSPACES, keep=True) as tree:
        applied = apply_patch(patch, tree)
        if not applied.applied:
            print(f"\nno branch: the patch would not re-apply "
                  f"({applied.stderr[-200:]})")
            return
        # Resolved against the real repo before asking, so the name in the
        # prompt is the name that will exist.
        branch = shipping.unique_branch(tree, branch)
        if not gate("commit to a branch"):
            return
        res = shipping.commit_patch(tree, branch, task.default_branch, title)
        print(f"\nbranch {branch}")
        print(f"  commit {res.commit[:12]}  (base {task.default_branch})")
        print(f"  tree   {tree}")

        if not (args.push or args.pr):
            print("  local only -- nothing was pushed. Add --push to send it.")
            return

        if not gate(f"push this branch to github.com/{task.repo}"):
            print("  the commit is still on disk; nothing left this machine.")
            return
        shipping.push_branch(tree, branch)
        print(f"  pushed to origin/{branch}")
        if args.pr:
            if not gate(f"open a pull request against {task.default_branch}"):
                print("  branch is pushed; no pull request was opened.")
                return
            url = shipping.open_pr(tree, branch, task.default_branch, title, body)
            print(f"  pull request: {url or '(opened)'}")


def cmd_mutants(args: argparse.Namespace) -> int:
    """Generate a frozen set of mutants: bugs with no published fix.

    Costs nothing -- no model is called. Every candidate is applied and the
    suite run, and only the ones that turn a green test red survive.
    """
    import hashlib

    from engine.graph import WORKSPACES
    from engine.eval.mutant_bench import commit_mutant, save, viable
    from engine.eval.mutate import generate
    from engine.eval.repo_grader import CachedImage, run_suite
    from engine.repo.front_door import open_repo
    from engine.repo.workspace import attempt_worktree

    task = open_repo(args.url, "mutant generation", WORKSPACES)
    print(f"\nrepo   {task.repo} @ {task.base_commit[:12]}")
    print(f"suite  {task.suite.kind} -- {task.suite.why}")
    if not task.suite.per_test:
        print("\nREFUSED: this suite cannot name which tests failed, so a "
              "mutant's blast radius is unknowable.")
        return 2

    key = hashlib.sha1(f"mut|{task.repo}@{task.base_commit}".encode()).hexdigest()[:12]
    cache = CachedImage(key)
    started = time.monotonic()
    kept = []
    try:
        with attempt_worktree(task.repo, task.base_commit, "mutgen", 1,
                              WORKSPACES) as tree:
            print("\nbaseline: the suite must be green before anything is broken")
            base = run_suite(tree, task.suite, cache)
            print(f"  {len(base.failures)} failing, exit {base.exit_code}, "
                  f"{time.monotonic() - started:.0f}s")
            if not base.reported:
                print("  the suite did not report -- nothing can be measured "
                      "against it")
                return 3
            if base.failures or base.exit_code not in (0, 1):
                # A mutant is "viable" if it turns a GREEN test red. Against a
                # baseline that is already broken -- missing dependencies, a
                # collection error -- every mutant looks harmless, because the
                # same failures happen with and without it. That is exactly
                # how this first reported 0% viable when the truth was 57%.
                print(f"\n  REFUSED: the baseline is not green "
                      f"({len(base.failures)} failing, exit {base.exit_code}).")
                print("  Mutants are graded by what they break, so nothing can "
                      "be measured against a suite that is already red.")
                print("  Fix the suite in the container first, then regenerate.")
                return 3

            pool = generate(tree, limit=args.pool, seed=args.seed)
            print(f"\n{len(pool)} candidates, filtering to {args.want} viable:")
            for m in pool:
                if len(kept) >= args.want:
                    break
                t = viable(m, tree, task.suite, base, cache, log=print)
                if t:
                    sha = commit_mutant(m, task.repo, task.base_commit, WORKSPACES)
                    kept.append(replace(t, commit=sha))
    finally:
        cache.close()

    out = REPO_ROOT / args.out
    save(kept, out)
    print(f"\n{len(kept)} viable of {len(pool)} tried "
          f"({len(kept) / max(1, len(pool)) * 100:.0f}%), "
          f"{time.monotonic() - started:.0f}s")
    print(f"frozen to {args.out} -- every experiment now runs these same bugs")
    for t in kept:
        print(f"  {t.mid:40} {t.commit[:8]}  breaks {len(t.fail_to_pass)}")
    return 0 if kept else 1


def cmd_run_mutants(args: argparse.Namespace) -> int:
    """Repair a frozen set of bugs that have no published fix.

    The point of the whole exercise: every SWE-bench number is threatened by
    memorisation (10 of 13 solved tasks in E1 reproduced the human patch
    verbatim). These bugs were written by a script, so there is nothing to
    recall -- the model gets failing test output and has to work it out.
    """
    import hashlib

    from engine.batch import run_spend_usd
    from engine.graph import WORKSPACES
    from engine.eval.mutant_bench import (load, mutant_grader, restored_original,
                                          scout_found_it)
    from engine.eval.repo_grader import CachedImage
    from engine.repo.front_door import open_repo

    tasks = load(REPO_ROOT / args.set)
    if not tasks:
        print(f"no mutants in {args.set}")
        return 1
    missing = [t.mid for t in tasks if not t.commit]
    if missing:
        print(f"{len(missing)} mutant(s) have no commit -- regenerate the set")
        return 2

    base = open_repo(args.url, "mutant repair", WORKSPACES)
    run_id = args.run_id or f"M_{datetime.now(timezone.utc):%Y%m%d_%H%M%S}"
    run_dir = REPO_ROOT / "runs" / run_id
    cfg = RunConfig(
        run_id=run_id,
        task_ids=[t.mid for t in tasks],
        model_for_role={r: args.model for r in
                        ("orchestrator", "scout", "builder", "tester", "reviewer", "scribe")},
        scout=not args.no_scout,
        reviewer_gate=not args.no_reviewer_gate,
        prompt_hashes=prompt_hashes(PROMPTS),
        **({"effort": args.effort, "reasoning_effort": args.effort}
           if args.effort else {}),
        **({"max_completion_tokens": args.max_tokens} if args.max_tokens else {}),
    )
    cfg.freeze_to(run_dir / "config.json")
    events = EventLog.for_run(run_dir, run_id)
    events.emit("run_started", agent="orchestrator", model=args.model,
                tasks=len(tasks),
                gates={"tester": cfg.tester_gate, "reviewer": cfg.reviewer_gate,
                       "scout": cfg.scout})

    if cfg.is_stub_run:
        print("=" * 68)
        print("STUB RUN -- canned responses. NOT A RESULT.")
        print("=" * 68)

    print(f"\n{len(tasks)} mutants of {base.repo}   model={args.model}   run={run_id}")
    key = hashlib.sha1(f"mut|{base.repo}@{base.base_commit}".encode()).hexdigest()[:12]
    cache = CachedImage(key)
    rows, extra = [], []
    spent = 0.0
    try:
        for i, mt in enumerate(tasks, 1):
            if args.max_usd and spent >= args.max_usd:
                print(f"\nbudget reached (${spent:.2f}) -- stopping with "
                      f"{len(rows)} of {len(tasks)} done")
                break
            print(f"\n--- {i}/{len(tasks)}  {mt.mid} "
                  f"({mt.path}:{mt.line}, breaks {len(mt.fail_to_pass)}) ---")
            task = replace(base, task_id=mt.mid, issue=mt.issue,
                           base_commit=mt.commit,
                           fail_to_pass=tuple(mt.fail_to_pass))
            grader = mutant_grader(task, WORKSPACES, cache)
            stub = _repo_stub(args) if cfg.is_stub_run else None
            try:
                row = run_one(task, cfg, run_dir, stub, args.max_attempts,
                              events=events, grader=grader)
            except WorkspaceError as exc:
                print(f"  WORKSPACE FAILURE: {exc}")
                continue
            rows.append(result_row(row))
            # From the ledger on disk, never a counter built from result rows:
            # result_row has no spend column, so summing it gave 0.00 forever
            # and --max-usd could not bind at any price. batch.py already had
            # this right (run_spend_usd), and this reuses it rather than
            # keeping a second, wronger, way to count money.
            spent = run_spend_usd([run_dir])
            extra.append({
                "mid": mt.mid, "path": mt.path, "line": mt.line,
                "operator": mt.operator, "broke": len(mt.fail_to_pass),
                "solved": row.get("status") == "shipped",
                "attempts": row.get("attempts"),
                # measured, not inferred: we know exactly which file we broke
                "scout_found_file": scout_found_it(run_dir, mt.mid, mt.path),
                # green tests are not the same as a repair -- see
                # restored_original's docstring for the case that proved it
                "restored_line": restored_original(run_dir, mt.mid,
                                                   mt.before, mt.after),
            })
    finally:
        cache.close()

    with (run_dir / "results.csv").open("w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=RESULT_FIELDS)
        w.writeheader()
        w.writerows(rows)
    if extra:
        with (run_dir / "mutants.csv").open("w", newline="") as fh:
            w = csv.DictWriter(fh, fieldnames=list(extra[0]))
            w.writeheader()
            w.writerows(extra)

    solved = sum(1 for e in extra if e["solved"])
    real = sum(1 for e in extra if e["solved"] and e["restored_line"])
    spent = run_spend_usd([run_dir])
    print("\n" + "=" * 68)
    print(f"tests green        {solved}/{len(extra)}")
    print(f"line restored      {real}/{len(extra)}   <- the honest score")
    if solved != real:
        print(f"  {solved - real} went green WITHOUT repairing the broken line:")
        for e in extra:
            if e["solved"] and not e["restored_line"]:
                print(f"    {e['mid']}  ({e['path']}:{e['line']})")
    print(f"${spent:.2f}   run={run_id}")
    seen = [e for e in extra if e["scout_found_file"] is not None]
    if seen:
        hit = [e for e in seen if e["scout_found_file"]]
        print(f"scout found the broken file in {len(hit)}/{len(seen)}")
        if hit and len(hit) != len(seen):
            sh = sum(1 for e in hit if e["solved"]) / len(hit) * 100
            miss = [e for e in seen if not e["scout_found_file"]]
            sm = sum(1 for e in miss if e["solved"]) / len(miss) * 100
            print(f"  solved when found: {sh:.0f}%    when missed: {sm:.0f}%")
    events.emit("run_finished", agent="orchestrator", task_arms=len(extra))
    print(f"artifacts: runs/{run_id}/")
    return 0


def cmd_report(args: argparse.Namespace) -> int:
    """Turn run logs into the tables. The only source of reported numbers."""
    dirs = [Path(d) for d in args.run_dirs]
    shared = common_tasks(dirs) if len(dirs) > 1 else None
    summaries = [summarize(d, only_tasks=shared) for d in dirs]
    if shared is not None:
        totals = []
        for d in dirs:
            with (d / "results.csv").open(newline="", encoding="utf-8") as h:
                totals.append(sum(1 for _ in csv.DictReader(h)))
        if len(set(totals)) > 1:
            print(f"*Restricted to the {len(shared)} tasks present in every arm "
                  f"(arms ran {', '.join(map(str, totals))}); an interrupted "
                  f"batch leaves one arm ahead.*\n")
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
    # override=False: a variable you exported for THIS command wins over the
    # file. The other way round means `ANTHROPIC_API_KEY=... garage run-repo`
    # silently does nothing, which is both surprising and a way to think you
    # are testing one key while spending on another.
    load_dotenv(REPO_ROOT / ".env", override=False)
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
    one.add_argument("--no-reviewer-gate", action="store_true",
                     help="drop the simplicity gate -- the OFF arm of E3 (FR-10)")
    one.add_argument("--stub-failures", default="",
                     help="stub only: comma list of failures to inject before "
                          "the fix, from prose,malformed,empty -- exercises the "
                          "retry loop offline")
    one.add_argument("--skip-preflight", action="store_true",
                     help="do not check key/Docker/git before starting")
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
    batch.add_argument("--no-scout", action="store_true",
                       help="builder sees the issue only -- the OFF arm of E2")
    batch.add_argument("--no-reviewer-gate", action="store_true",
                       help="drop the simplicity gate -- the OFF arm of E3 "
                            "(the ponytail experiment)")
    batch.add_argument("--max-usd", type=float, default=5.0,
                       help="hard ceiling for the WHOLE batch, resumes included "
                            "(default: $5). Checked before each task against "
                            "the ledger on disk.")
    batch.add_argument("--keep-images", action="store_true",
                       help="skip pruning -- needs ~4.2 GB of disk per task")
    batch.add_argument("--stub-failures", default="")
    batch.add_argument("--stub-reject", action="store_true")
    batch.set_defaults(func=cmd_run_batch)

    repo = sub.add_parser("run-repo", help="repair a real GitHub repo (front door)")
    repo.add_argument("--url", required=True, help="github.com/owner/name")
    repo.add_argument("--issue", required=True, help="what is wrong, in prose")
    repo.add_argument("--model", default=STUB_MODEL)
    repo.add_argument("--run-id", default=None)
    repo.add_argument("--max-attempts", type=int, default=None)
    repo.add_argument("--no-scout", action="store_true")
    repo.add_argument("--no-reviewer-gate", action="store_true")
    repo.add_argument("--stub-failures", default="")
    repo.add_argument("--stub-reject", action="store_true")
    repo.add_argument("--branch", action="store_true",
                      help="commit the fix to a local garage/ branch")
    repo.add_argument("--push", action="store_true",
                      help="WRITES TO THE REMOTE: push that branch")
    repo.add_argument("--yes", action="store_true",
                      help="approve repo writes without asking -- for genuinely "
                           "unattended runs. Recorded in the log as assumed_yes, "
                           "never as a human decision.")
    repo.add_argument("--skip-preflight", action="store_true",
                      help="do not check key/Docker/git before starting")
    repo.add_argument("--pr", action="store_true",
                      help="WRITES TO THE REMOTE: open a pull request via gh")
    repo.set_defaults(func=cmd_run_repo)

    rep = sub.add_parser("report", help="turn run logs into the M1-M3 tables")
    rep.add_argument("run_dirs", nargs="+", help="one or more runs/<id> paths")
    rep.add_argument("--title", default="Results")
    rep.add_argument("--out", default=None, help="also write the markdown here")
    rep.set_defaults(func=cmd_report)

    mut = sub.add_parser("mutants",
                         help="generate a frozen set of bugs with no published fix")
    mut.add_argument("--url", required=True, help="repo to break, e.g. github.com/you/thing")
    mut.add_argument("--pool", type=int, default=80, help="candidates to try")
    mut.add_argument("--want", type=int, default=50, help="viable mutants to keep")
    mut.add_argument("--seed", type=int, default=1, help="same seed, same set")
    mut.add_argument("--out", default="experiments/mutants/set.json")
    mut.set_defaults(func=cmd_mutants)

    rm = sub.add_parser("run-mutants", help="repair a frozen set of injected bugs")
    rm.add_argument("--set", required=True, help="a manifest from `mutants`")
    rm.add_argument("--url", required=True, help="the repo the set was made from")
    rm.add_argument("--model", default=STUB_MODEL)
    rm.add_argument("--run-id", default=None)
    rm.add_argument("--max-attempts", type=int, default=None)
    rm.add_argument("--max-usd", type=float, default=5.0,
                    help="hard ceiling for the whole set (default: $5)")
    rm.add_argument("--effort", default=None,
                    help="reasoning effort: low | medium | high. The default is "
                         "tuned for hard SWE-bench tasks and spends ~26k output "
                         "tokens on a one-line bug.")
    rm.add_argument("--max-tokens", type=int, default=None,
                    help="max_completion_tokens for this run")
    rm.add_argument("--no-scout", action="store_true", help="the OFF arm of E2")
    rm.add_argument("--no-reviewer-gate", action="store_true", help="the OFF arm of E3")
    rm.add_argument("--stub-failures", default="")
    rm.add_argument("--stub-reject", action="store_true")
    rm.set_defaults(func=cmd_run_mutants)

    doc = sub.add_parser("doctor", help="check this machine can do a real run")
    doc.add_argument("--model", default="claude-sonnet-5",
                     help="the model you intend to run (default: claude-sonnet-5)")
    doc.add_argument("--pr", action="store_true",
                     help="also require the GitHub CLI, for opening pull requests")
    doc.add_argument("--no-docker", action="store_true",
                     help="skip the Docker check (benchmark mode without grading)")
    doc.add_argument("--offline", action="store_true",
                     help="skip the live key check -- no API call at all")
    doc.set_defaults(func=cmd_doctor)

    args = parser.parse_args(argv)
    return int(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main())
