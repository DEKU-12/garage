"""TaskState / Attempt and their helpers (TAD §3.1).

Build week: 2. State lives here and flows through the graph -- no globals, no
singletons (rules.md §2.2). Nodes return partial updates; nothing mutates a
neighbour's data.

Run-constant things (config, run dir, stub backend) are deliberately NOT in
state: the graph builder closes over them. State is the task's story, not the
process's plumbing.
"""

from __future__ import annotations

from typing import Any, TypedDict


class Attempt(TypedDict, total=False):
    """One builder attempt, from prompt to verdict."""

    n: int
    patch: str
    patch_applied: bool
    apply_mode: str
    failure: str  # "" | "patch_rejected" | "apply_failed" | "model_error"
    error: str  # the failure's own words, so a post-mortem needs no log
    test_verdict: str | None  # "pass" | "fail" | None (not run)
    test_output: str | None  # truncated, for feedback
    review_verdict: str | None  # "accept" | "reject" | None
    review_reason: str | None  # ladder rung + paragraph
    hunk_repairs: list[str]
    usage: dict[str, Any]
    wall_ms: int


class TaskState(TypedDict, total=False):
    """Everything known about one task in flight."""

    task_id: str
    issue: str
    repo: str
    base_commit: str
    fail_to_pass: list[str]
    image: str

    context_pack: list[dict[str, Any]]
    context: str  # the rendered pack the builder actually sees
    feedback: str | None  # why the last attempt failed, fed to the next

    attempts: list[Attempt]
    correctness_retries: int
    simplicity_retries: int

    status: str  # running | shipped | failed_tests | failed_review |
                 # patch_apply_error | budget_exceeded | crashed
    failure_type: str
    spend_usd: float
    prompt_tokens: int
    completion_tokens: int


# Terminal statuses, one per branch of the failure taxonomy (TAD §8.2).
TERMINAL = {
    "shipped",
    "unverified",   # repo mode: no regressions, but nothing proves it fixed anything
    "failed_tests",
    "failed_review",
    "patch_apply_error",
    "budget_exceeded",
    "crashed",
}


def new_state(task_id: str, issue: str, repo: str, base_commit: str,
              fail_to_pass: list[str], image: str) -> TaskState:
    """A task at the starting line."""
    return TaskState(
        task_id=task_id,
        issue=issue,
        repo=repo,
        base_commit=base_commit,
        fail_to_pass=fail_to_pass,
        image=image,
        context_pack=[],
        context="",
        feedback=None,
        attempts=[],
        correctness_retries=0,
        simplicity_retries=0,
        status="running",
        failure_type="",
        spend_usd=0.0,
        prompt_tokens=0,
        completion_tokens=0,
    )


def last_attempt(state: TaskState) -> Attempt | None:
    attempts = state.get("attempts") or []
    return attempts[-1] if attempts else None


def solved(state: TaskState) -> bool:
    return state.get("status") == "shipped"
