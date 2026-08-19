"""The state machine end to end -- stub model, Docker mocked (TAD §8.5).

No API, no network, no containers, no bare clones. This is the test that must
stay fast and green in CI: it walks every routing path in engine/graph.py,
including the ones that only happen at a retry cap.
"""

from __future__ import annotations

import subprocess
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator

import pytest

from engine.agents.stub import (
    BARE_HUNK,
    MALFORMED_DIFF,
    PROSE_ONLY,
    REJECT_RUNG_3,
    StubBackend,
    builder_script,
    reviewer_script,
)
from engine.config import RunConfig
from engine.eval.grader import GradeResult
from engine.state import new_state

SOURCE = "def greet():\n    return 'hi'\n"
GOLD = """diff --git a/hello.py b/hello.py
--- a/hello.py
+++ b/hello.py
@@ -1,2 +1,2 @@
 def greet():
-    return 'hi'
+    return 'hello'
"""


@pytest.fixture
def fake_repo(tmp_path: Path) -> Path:
    tree = tmp_path / "tree"
    tree.mkdir()
    (tree / "hello.py").write_text(SOURCE)
    run = lambda *a: subprocess.run(["git", *a], cwd=tree, capture_output=True, check=True)
    run("init", "-q", "-b", "main")
    run("-c", "user.name=t", "-c", "user.email=t@t", "add", "hello.py")
    run("-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "-m", "init")
    return tree


@pytest.fixture
def harness(monkeypatch: pytest.MonkeyPatch, fake_repo: Path, tmp_path: Path):
    """Build a graph whose worktrees and grader are local and instant."""
    import engine.graph as g

    @contextmanager
    def fake_worktree(repo, commit, task_id, attempt, root, keep=False) -> Iterator[Path]:
        # Fresh checkout per attempt (ADR-4), reproduced without git worktree.
        # --hard is required: `git checkout -- .` restores from the INDEX, and
        # `git apply --3way` stages its change there, so a soft reset leaves
        # the previous attempt's patch in place and the next apply conflicts.
        subprocess.run(["git", "reset", "--hard", "-q", "HEAD"],
                       cwd=fake_repo, capture_output=True)
        yield fake_repo

    monkeypatch.setattr(g, "attempt_worktree", fake_worktree)

    def make(verdicts: list[str], **cfg_kw: Any):
        """verdicts: the grader's answers, in order; the last one repeats."""
        calls = {"n": 0}

        def fake_grade(task_id, patch, run_id, work_dir, image="", **kw):
            i = calls["n"]
            calls["n"] += 1
            verdict = verdicts[min(i, len(verdicts) - 1)]
            return GradeResult(
                task_id=task_id, verdict=verdict, resolved=verdict == "pass",
                reason="", log_tail="1 failed" if verdict == "fail" else "ok",
                report_path=tmp_path / "r.json", test_output_path=None, wall_ms=1,
            )

        monkeypatch.setattr(g, "grade", fake_grade)
        cfg = RunConfig(
            run_id="t", task_ids=["t__t-1"],
            model_for_role={r: "stub" for r in
                            ("orchestrator", "scout", "builder", "tester",
                             "reviewer", "scribe")},
            scout=False, **cfg_kw,
        )
        return cfg, calls

    return make, tmp_path


def _state() -> dict:
    return new_state("t__t-1", "greet returns hi", "o/r", "abc", ["test_greet"], "img")


def _run(cfg, stub, run_dir: Path):
    from engine.graph import build_graph
    machine = build_graph(cfg, run_dir, stub, log=lambda *_: None)
    return machine.invoke(_state(), {"recursion_limit": 40})


# --- the happy path -------------------------------------------------------

def test_clean_solve_ships(harness) -> None:
    make, run_dir = harness
    cfg, _ = make(["pass"])
    stub = StubBackend(scripts={"builder": builder_script(GOLD),
                                "reviewer": reviewer_script()})
    final = _run(cfg, stub, run_dir)
    assert final["status"] == "shipped"
    assert len(final["attempts"]) == 1
    assert final["attempts"][0]["review_verdict"] == "accept"


# --- correctness gate -----------------------------------------------------

def test_failing_tests_send_the_work_back(harness) -> None:
    make, run_dir = harness
    cfg, calls = make(["fail", "pass"])
    stub = StubBackend(scripts={"builder": builder_script(GOLD),
                                "reviewer": reviewer_script()})
    final = _run(cfg, stub, run_dir)
    assert final["status"] == "shipped"
    assert len(final["attempts"]) == 2
    assert calls["n"] == 2


def test_correctness_cap_means_one_run_plus_three_retries(harness) -> None:
    """max_correctness_retries=3 must allow FOUR builder runs, not three."""
    make, run_dir = harness
    cfg, _ = make(["fail"])
    stub = StubBackend(scripts={"builder": builder_script(GOLD),
                                "reviewer": reviewer_script()})
    final = _run(cfg, stub, run_dir)
    assert len(final["attempts"]) == 4
    assert final["status"] == "failed_tests"


def test_a_patch_that_never_applies_is_its_own_failure_type(harness) -> None:
    """'cannot format a diff' and 'cannot fix a bug' are different findings."""
    make, run_dir = harness
    cfg, calls = make(["pass"])
    stub = StubBackend(scripts={"builder": [PROSE_ONLY] * 6,
                                "reviewer": reviewer_script()})
    final = _run(cfg, stub, run_dir)
    assert final["failure_type"] == "patch_apply_error"
    assert calls["n"] == 0, "a patch that never applied must never reach Docker"


def test_apply_failure_feeds_gits_words_back_to_the_builder(harness) -> None:
    make, run_dir = harness
    cfg, _ = make(["pass"])
    stub = StubBackend(scripts={"builder": builder_script(GOLD, before=[MALFORMED_DIFF]),
                                "reviewer": reviewer_script()})
    final = _run(cfg, stub, run_dir)
    assert final["status"] == "shipped"
    assert len(final["attempts"]) == 2


# --- simplicity gate ------------------------------------------------------

def test_rejection_rebuilds_once(harness) -> None:
    make, run_dir = harness
    cfg, _ = make(["pass"])
    stub = StubBackend(scripts={"builder": builder_script(GOLD),
                                "reviewer": reviewer_script(before=[REJECT_RUNG_3])})
    final = _run(cfg, stub, run_dir)
    assert len(final["attempts"]) == 2
    assert final["attempts"][0]["review_verdict"] == "reject"
    assert final["status"] == "shipped"


def test_a_correct_patch_ships_even_when_the_reviewer_keeps_rejecting(harness) -> None:
    """ADR-7: simplicity never discards a patch the tests approved."""
    make, run_dir = harness
    cfg, _ = make(["pass"])
    stub = StubBackend(scripts={"builder": builder_script(GOLD),
                                "reviewer": [REJECT_RUNG_3]})
    final = _run(cfg, stub, run_dir)
    assert final["status"] == "shipped"
    assert final["attempts"][-1]["review_verdict"] == "reject"


# --- gates off (FR-10) ----------------------------------------------------

def test_tester_gate_off_gets_exactly_one_shot(harness) -> None:
    """The OFF arm has no edge back to the builder -- it cannot retry."""
    make, run_dir = harness
    cfg, calls = make(["fail"], tester_gate=False)
    stub = StubBackend(scripts={"builder": builder_script(GOLD),
                                "reviewer": reviewer_script()})
    final = _run(cfg, stub, run_dir)
    assert len(final["attempts"]) == 1
    assert calls["n"] == 1, "still graded -- the score IS the experiment"
    assert final["status"] != "shipped"


def test_reviewer_gate_off_never_reviews(harness) -> None:
    make, run_dir = harness
    cfg, _ = make(["pass"], reviewer_gate=False)
    stub = StubBackend(scripts={"builder": builder_script(GOLD),
                                "reviewer": [REJECT_RUNG_3]})
    final = _run(cfg, stub, run_dir)
    assert final["status"] == "shipped"
    assert final["attempts"][0]["review_verdict"] is None


def test_gate_off_removes_the_retry_edge_from_the_graph(harness) -> None:
    """Structural, not behavioural: the OFF arm cannot retry even in principle."""
    from engine.graph import build_graph
    make, run_dir = harness

    def back_edges(**kw):
        cfg, _ = make(["pass"], **kw)
        g = build_graph(cfg, run_dir, log=lambda *_: None).get_graph()
        return {(e.source, e.target) for e in g.edges
                if e.target == "builder" and e.source != "__start__"}

    # Reviewer on adds its own retry edge; the tester gate owns only its own.
    assert back_edges() == {("tester", "builder"), ("reviewer", "builder")}
    assert back_edges(tester_gate=False) == {("reviewer", "builder")}
    assert back_edges(tester_gate=False, reviewer_gate=False) == set()


# --- budget (FR-12) -------------------------------------------------------

def test_token_cap_ends_the_task_cleanly_not_as_a_crash(harness) -> None:
    make, run_dir = harness
    cfg, _ = make(["fail"], per_task_token_cap=1)
    stub = StubBackend(scripts={"builder": builder_script(GOLD),
                                "reviewer": reviewer_script()})
    final = _run(cfg, stub, run_dir)
    assert final["status"] == "budget_exceeded"
    assert final["failure_type"] == "budget_exceeded"


def test_a_bare_hunk_header_is_repaired_and_still_ships(harness) -> None:
    """Models write "@@" with no line numbers; the edit is right, the
    arithmetic is missing. Dropping this repair once cost a real solve and no
    test noticed, because every scripted diff was well-formed."""
    make, run_dir = harness
    cfg, _ = make(["pass"])
    stub = StubBackend(scripts={"builder": [BARE_HUNK],
                                "reviewer": reviewer_script()})
    final = _run(cfg, stub, run_dir)
    assert final["status"] == "shipped"
    assert final["attempts"][0]["hunk_repairs"]


def test_repair_can_be_turned_off_and_then_the_bare_hunk_fails(harness) -> None:
    """The flag has to actually matter, or its measured contribution is a lie."""
    make, run_dir = harness
    cfg, _ = make(["pass"], repair_hunks=False)
    stub = StubBackend(scripts={"builder": [BARE_HUNK],
                                "reviewer": reviewer_script()})
    final = _run(cfg, stub, run_dir)
    assert final["status"] != "shipped"
    assert final["failure_type"] == "patch_apply_error"


def test_gate_off_never_retries_even_when_the_reviewer_rejects(harness) -> None:
    """The OFF arm's definition is 'no retries'. The reviewer must not add one.

    Routing tester -> reviewer unconditionally let the reviewer reject a
    FAILING patch and send it back, giving the OFF arm two builder runs. That
    flatters the baseline and shrinks the measured gate lift -- it showed up as
    attempts=2 in the E1 pilot's OFF arm, which is supposed to be all 1s.
    """
    make, run_dir = harness
    cfg, _ = make(["fail"], tester_gate=False)
    stub = StubBackend(scripts={"builder": builder_script(GOLD),
                                "reviewer": [REJECT_RUNG_3]})
    final = _run(cfg, stub, run_dir)
    assert len(final["attempts"]) == 1


def test_gate_off_does_not_review_a_patch_the_tests_rejected(harness) -> None:
    """Correctness is not the reviewer's call -- it never sees a failing patch."""
    make, run_dir = harness
    cfg, _ = make(["fail"], tester_gate=False)
    stub = StubBackend(scripts={"builder": builder_script(GOLD),
                                "reviewer": reviewer_script()})
    final = _run(cfg, stub, run_dir)
    assert final["attempts"][0]["review_verdict"] is None


def test_gate_off_still_reviews_a_patch_that_passed(harness) -> None:
    make, run_dir = harness
    cfg, _ = make(["pass"], tester_gate=False)
    stub = StubBackend(scripts={"builder": builder_script(GOLD),
                                "reviewer": reviewer_script()})
    final = _run(cfg, stub, run_dir)
    assert final["status"] == "shipped"
    assert final["attempts"][0]["review_verdict"] == "accept"
