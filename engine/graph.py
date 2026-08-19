"""LangGraph wiring: nodes and edges only, no business logic (TAD §3.2).

Build week: 2. ADR-2: hand-written graph, not CrewAI/AutoGen -- the explicit
state machine is the portfolio piece, and every routing decision here is one
short function you can read in full.

    START -> scout -> builder -> tester -> reviewer -> ship -> END
                        ^          |          |
                        +----------+----------+   retries, bounded

Termination proof: the builder runs at most
    1 + max_correctness_retries (3) + max_simplicity_retries (1) = 5 times.

**Gates are edge rewiring, not branches.** With a gate off, the edge that
returns to the builder is never added to the graph, so the OFF configuration is
structurally incapable of retrying -- which is what makes the A/B in E1 and E3
honest rather than a promise. You can read the graph and see the difference.

Emits: agent_activated, agent_done, handoff, context_pack_ready,
patch_produced, patch_apply_error, tests_run, gate_verdict, retry, shipped,
task_failed (week 3, when events.py is retrofitted).
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any, Callable

from langgraph.graph import END, START, StateGraph

from engine.accounting import ledger as ledger_mod
from engine.agents.builder import build_patch
from engine.agents.reviewer import review_patch
from engine.agents.scout import build_context_pack, render_pack
from engine.agents.stub import StubBackend
from engine.config import RunConfig
from engine.errors import GradingInfraError, ModelCallError, PatchError
from engine.eval.grader import grade
from engine.repo.patch import (
    apply_patch,
    extract_diff,
    repair_hunk_headers,
    tree_diff,
    validate_diff,
)
from engine.repo.workspace import attempt_worktree
from engine.state import Attempt, TaskState, last_attempt

WORKSPACES = Path(__file__).resolve().parents[1] / "workspaces"


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _attempt_dir(run_dir: Path, task_id: str, n: int) -> Path:
    return run_dir / "tasks" / task_id / "attempts" / str(n)


def _bill(run_dir: Path, task_id: str, attempt: int, role: str, usage: Any) -> None:
    """One ledger row per model call, written immediately.

    Written now rather than accumulated in memory: a crashed run must still
    account for what it already spent (NFR-4). $/solved is summed from these
    rows by report/aggregate.py, never from a running total.
    """
    ledger_mod.append(run_dir / "ledger.csv", [ledger_mod.row_for(
        task_id, attempt, role, usage.model,
        usage.prompt_tokens, usage.completion_tokens, usage.latency_ms,
    )])


def _tokens_used(state: TaskState) -> int:
    return state.get("prompt_tokens", 0) + state.get("completion_tokens", 0)


def build_graph(
    cfg: RunConfig,
    run_dir: Path,
    stub: StubBackend | None = None,
    log: Callable[[str], None] = print,
) -> Any:
    """Compile the state machine for one RunConfig.

    Run-constant things are closed over rather than carried in state: the state
    is the task's story, not the process's plumbing.
    """

    # ---------------------------------------------------------------- nodes

    def scout_node(state: TaskState) -> dict[str, Any]:
        """Find the code the fix belongs in. Read-only on the repo."""
        with attempt_worktree(state["repo"], state["base_commit"],
                              state["task_id"], 0, WORKSPACES) as tree:
            pack = build_context_pack(state["issue"], tree, cfg.context_token_budget)
            context = render_pack(pack)
        _write(run_dir / "tasks" / state["task_id"] / "context_pack.md", context)
        log(f"  scout: {len(pack)} span(s), ~{len(context) // 4} tokens")
        for entry in pack:
            log(f"    {entry.file}:{entry.start}-{entry.end}")
        return {
            "context": context,
            "context_pack": [
                {"file": e.file, "start": e.start, "end": e.end, "why": e.why}
                for e in pack
            ],
        }

    def builder_node(state: TaskState) -> dict[str, Any]:
        """Write a patch and get it onto a clean checkout."""
        n = len(state.get("attempts", [])) + 1
        started = time.monotonic()
        adir = _attempt_dir(run_dir, state["task_id"], n)
        log(f"\n--- attempt {n}/{cfg.max_builder_runs} ---")

        attempt = Attempt(n=n, patch="", patch_applied=False, apply_mode="",
                          failure="", test_verdict=None, test_output=None,
                          review_verdict=None, review_reason=None, usage={})

        # FR-12: budget checked at node entry, never mid-call.
        if cfg.per_task_token_cap and _tokens_used(state) >= cfg.per_task_token_cap:
            log(f"  budget: {_tokens_used(state)} tokens used, cap is "
                f"{cfg.per_task_token_cap} -- stopping")
            attempt["failure"] = "budget_exceeded"
            attempt["wall_ms"] = 0
            return {"attempts": [*state.get("attempts", []), attempt],
                    "status": "budget_exceeded", "failure_type": "budget_exceeded"}

        try:
            raw, user_msg, usage = build_patch(
                state["issue"], state.get("context", ""), cfg, stub,
                state.get("feedback"),
            )
        except ModelCallError as exc:
            log(f"  model call failed: {exc}")
            attempt["failure"] = "model_error"
            attempt["wall_ms"] = int((time.monotonic() - started) * 1000)
            _write(adir / "meta.json", json.dumps({"error": str(exc)}, indent=2))
            return {"attempts": [*state.get("attempts", []), attempt],
                    "status": "crashed", "failure_type": "model_error"}

        _bill(run_dir, state["task_id"], n, "builder", usage)
        attempt["usage"] = {"prompt_tokens": usage.prompt_tokens,
                            "completion_tokens": usage.completion_tokens,
                            "latency_ms": usage.latency_ms,
                            "finish_reason": usage.finish_reason}
        _write(adir / "prompt_builder.md", user_msg)
        _write(adir / "response.md", raw)
        log(f"  builder responded ({len(raw)} chars, {usage.latency_ms}ms)")

        update: dict[str, Any] = {
            "prompt_tokens": state.get("prompt_tokens", 0) + usage.prompt_tokens,
            "completion_tokens": state.get("completion_tokens", 0) + usage.completion_tokens,
        }

        with attempt_worktree(state["repo"], state["base_commit"],
                              state["task_id"], n, WORKSPACES) as tree:
            try:
                diff = extract_diff(raw)
                if cfg.repair_hunks:
                    # Models routinely emit a bare "@@" with no line numbers.
                    # The edit is right; only the arithmetic is missing, and we
                    # can compute it from the file. Flag-gated so its
                    # contribution stays measurable like any other change.
                    diff, repairs = repair_hunk_headers(diff, tree)
                    if repairs:
                        log(f"  repaired {len(repairs)} hunk header(s)")
                        attempt["hunk_repairs"] = repairs
                validate_diff(diff, tree)
            except PatchError as exc:
                log(f"  patch rejected: {exc}")
                attempt["failure"] = "patch_rejected"
                attempt["wall_ms"] = int((time.monotonic() - started) * 1000)
                update["feedback"] = str(exc)
                update["attempts"] = [*state.get("attempts", []), attempt]
                return update

            applied = apply_patch(diff, tree)
            if not applied.applied:
                log(f"  git apply failed: {applied.stderr.splitlines()[:1]}")
                attempt["failure"] = "apply_failed"
                attempt["wall_ms"] = int((time.monotonic() - started) * 1000)
                update["feedback"] = applied.stderr  # git's own words, verbatim
                update["attempts"] = [*state.get("attempts", []), attempt]
                return update

            submission = tree_diff(tree)

        _write(adir / "patch.diff", submission)
        attempt["patch"] = submission
        attempt["patch_applied"] = True
        attempt["apply_mode"] = applied.mode
        attempt["wall_ms"] = int((time.monotonic() - started) * 1000)
        log(f"  applied ({applied.mode}), {len(submission.splitlines())} line patch")
        update["attempts"] = [*state.get("attempts", []), attempt]
        return update

    def tester_node(state: TaskState) -> dict[str, Any]:
        """Grade the patch in Docker. Not an LLM (TAD §3.3)."""
        attempts = list(state.get("attempts", []))
        attempt = dict(attempts[-1])

        if not attempt.get("patch_applied"):
            attempt["test_verdict"] = None  # nothing to grade
            attempts[-1] = attempt
            return {"attempts": attempts}

        try:
            result = grade(state["task_id"], attempt["patch"],
                           f"{cfg.run_id}_a{attempt['n']}", run_dir,
                           image=state.get("image", ""))
        except GradingInfraError as exc:
            log(f"  GRADING INFRA FAILURE: {exc}")
            attempt["test_verdict"] = None
            attempt["failure"] = "grading_infra_error"
            attempts[-1] = attempt
            # Infra, never the model's fault (rules.md §3.1).
            return {"attempts": attempts, "status": "crashed",
                    "failure_type": "crashed"}

        _write(_attempt_dir(run_dir, state["task_id"], attempt["n"]) / "test_output.txt",
               result.log_tail)
        attempt["test_verdict"] = result.verdict
        attempt["test_output"] = result.log_tail[-2000:]
        attempts[-1] = attempt
        log(f"  tests: {result.verdict} ({result.wall_ms / 1000:.1f}s)")

        if result.verdict == "fail":
            return {"attempts": attempts,
                    "feedback": "The patch applied cleanly but the tests still "
                                f"fail:\n\n{result.log_tail[-2000:]}"}
        return {"attempts": attempts}

    def reviewer_node(state: TaskState) -> dict[str, Any]:
        """The simplicity gate. Cannot judge correctness."""
        attempts = list(state.get("attempts", []))
        attempt = dict(attempts[-1])

        try:
            review, user_msg, usage = review_patch(attempt["patch"], cfg, stub)
        except ModelCallError as exc:
            # The junior gate must never sink a correct patch (TAD §3.3).
            log(f"  reviewer unavailable ({exc}) -- treating as ACCEPT")
            attempt["review_verdict"] = "accept"
            attempt["review_reason"] = "reviewer unavailable"
            attempts[-1] = attempt
            return {"attempts": attempts}

        _bill(run_dir, state["task_id"], attempt["n"], "reviewer", usage)
        adir = _attempt_dir(run_dir, state["task_id"], attempt["n"])
        _write(adir / "prompt_reviewer.md", user_msg)
        _write(adir / "review.md", review.raw)

        attempt["review_verdict"] = review.verdict
        attempt["review_reason"] = review.reason
        attempts[-1] = attempt
        note = " (unparseable, treated as ACCEPT)" if review.parse_warning else ""
        log(f"  review: {review.verdict}"
            + (f" rung {review.rung}" if review.rung else "") + note)

        update: dict[str, Any] = {
            "attempts": attempts,
            "prompt_tokens": state.get("prompt_tokens", 0) + usage.prompt_tokens,
            "completion_tokens": state.get("completion_tokens", 0) + usage.completion_tokens,
        }
        if review.verdict == "reject":
            update["feedback"] = (
                f"The tests pass, but the patch was rejected for simplicity "
                f"(ladder rung {review.rung}): {review.reason}\n\n"
                "Produce a smaller patch that still passes the tests."
            )
        return update

    def ship_node(state: TaskState) -> dict[str, Any]:
        attempt = last_attempt(state)
        if attempt and attempt.get("test_verdict") == "pass":
            return {"status": "shipped", "failure_type": ""}
        return {"status": state.get("status") or "failed_tests",
                "failure_type": state.get("failure_type") or "failed_tests"}

    def fail_node(state: TaskState) -> dict[str, Any]:
        """Every non-shipped ending lands in exactly one bucket (TAD §8.2)."""
        attempts = state.get("attempts", [])
        if state.get("status") in {"crashed", "budget_exceeded"}:
            return {}
        never_applied = all(not a.get("patch_applied") for a in attempts)
        failure = "patch_apply_error" if never_applied else "failed_tests"
        return {"status": failure, "failure_type": failure}

    # ------------------------------------------------------------- routing

    def after_builder(state: TaskState) -> str:
        """A dead attempt never reaches Docker; it goes straight to routing."""
        if state.get("status") in {"crashed", "budget_exceeded"}:
            return "fail"
        attempt = last_attempt(state)
        return "tester" if attempt and attempt.get("patch_applied") else "recheck"

    # Retries TAKEN, not failures seen. The first failure has not been retried
    # yet, so comparing raw failure counts against the cap loses one attempt at
    # every gate: with max_correctness_retries=3 the builder would run 3 times
    # instead of 4, and a max_simplicity_retries=1 reviewer would ship anyway
    # on its first rejection without ever asking for a smaller patch.
    def correctness_retries_taken(state: TaskState) -> int:
        failures = sum(
            1 for a in state.get("attempts", [])
            if not a.get("patch_applied") or a.get("test_verdict") == "fail"
        )
        return max(0, failures - 1)

    def simplicity_retries_taken(state: TaskState) -> int:
        rejections = sum(
            1 for a in state.get("attempts", [])
            if a.get("review_verdict") == "reject"
        )
        return max(0, rejections - 1)

    def after_tester(state: TaskState) -> str:
        if state.get("status") == "crashed":
            return "fail"
        attempt = last_attempt(state)
        assert attempt is not None
        if not attempt.get("patch_applied") or attempt.get("test_verdict") == "fail":
            if correctness_retries_taken(state) < cfg.max_correctness_retries:
                log(f"  retry {correctness_retries_taken(state) + 1}"
                    f"/{cfg.max_correctness_retries}")
                return "builder"
            return "fail"
        return "reviewer" if cfg.reviewer_gate else "ship"

    def after_reviewer(state: TaskState) -> str:
        attempt = last_attempt(state)
        assert attempt is not None
        if attempt.get("review_verdict") == "reject":
            if simplicity_retries_taken(state) < cfg.max_simplicity_retries:
                return "builder"
            # ADR-7: simplicity never blocks a correct patch at the cap. The
            # verdict is recorded either way -- that record is what E3 measures.
            log("  simplicity cap reached -- shipping the correct patch anyway")
            return "ship"
        return "ship"

    # --------------------------------------------------------------- wiring

    graph = StateGraph(TaskState)
    graph.add_node("builder", builder_node)
    graph.add_node("tester", tester_node)
    graph.add_node("ship", ship_node)
    graph.add_node("fail", fail_node)

    if cfg.scout:
        graph.add_node("scout", scout_node)
        graph.add_edge(START, "scout")
        graph.add_edge("scout", "builder")
    else:
        # Scout OFF is the absence of a node, not a flag inside one (FR-10).
        graph.add_edge(START, "builder")

    if cfg.reviewer_gate:
        graph.add_node("reviewer", reviewer_node)
        graph.add_conditional_edges(
            "reviewer", after_reviewer, {"builder": "builder", "ship": "ship"}
        )

    if cfg.tester_gate:
        # The gate IS the retry edge back to the builder. With the gate on,
        # a failing test can send the work back.
        graph.add_conditional_edges(
            "builder", after_builder,
            {"tester": "tester", "recheck": "tester", "fail": "fail"},
        )
        graph.add_conditional_edges(
            "tester", after_tester,
            {"builder": "builder", "reviewer": "reviewer" if cfg.reviewer_gate else "ship",
             "ship": "ship", "fail": "fail"},
        )
    else:
        # Gate OFF: still graded, because the score is the experiment -- but
        # there is NO edge back to the builder. One shot, whatever it produces.
        graph.add_edge("builder", "tester")
        graph.add_edge("tester", "reviewer" if cfg.reviewer_gate else "ship")

    graph.add_edge("ship", END)
    graph.add_edge("fail", END)
    return graph.compile()
