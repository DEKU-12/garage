"""Turning mutants into gradeable tasks.

Build week: 6.

A mutation is only a task if it actually breaks something. Applying one and
finding the suite still green means the line was never exercised -- "fixing" it
would be unmeasurable, and counting it would quietly inflate or deflate every
number downstream depending on which way the model guessed.

So each mutant is run before it is offered, and only the ones that turn a green
test red survive. What comes out the other side is a bug with a known blast
radius: a `fail_to_pass` list we produced ourselves, which is the thing repo
mode has to do without and the thing that makes grading a comparison rather
than an inference.

All of this shares ONE prepared container image for the repo (see
CachedImage), so the dependency install happens once for the whole set rather
than once per mutant. On NL2SQL that is the difference between six minutes and
five hours.

Emits: nothing -- the caller emits around it.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from pathlib import Path

from engine.errors import GradingInfraError, WorkspaceError
from engine.eval.mutate import Mutant
from engine.eval.repo_grader import SuiteRun, run_suite


@dataclass(frozen=True)
class MutantTask:
    """A mutant that provably breaks something, ready to be repaired."""

    mid: str
    path: str
    line: int
    operator: str
    before: str
    after: str
    fail_to_pass: list[str]     # went green -> red. The definition of fixed.
    issue: str                  # what the garage is told
    commit: str = ""            # the broken state, as a real commit

    def to_json(self) -> dict:
        return asdict(self)


def describe(m: Mutant, broke: list[str], output: str) -> str:
    """What the agent is told.

    The failing test output, verbatim, and nothing else -- no hint about which
    line changed or what the mutation was. This is exactly what an agent woken
    by a red CI build has to work with, and giving it more would measure a
    situation nobody is ever in.
    """
    listed = "\n".join(f"  - {t}" for t in broke[:12])
    return (
        f"The test suite is failing. {len(broke)} test(s) that passed on the "
        f"previous commit now fail:\n{listed}\n\n"
        f"Test output:\n\n{output[-2500:]}\n\n"
        "Find the cause and fix it. Do not change or delete the tests."
    )


def viable(mutant: Mutant, tree: Path, suite, baseline: SuiteRun,
           runner, log=print) -> MutantTask | None:
    """Apply, run, and keep only if it broke something that was passing."""
    target = Path(tree) / mutant.path
    original = target.read_text(encoding="utf-8")
    try:
        target.write_text(mutant.source, encoding="utf-8")
        after = run_suite(tree, suite, runner)
    finally:
        target.write_text(original, encoding="utf-8")

    if not after.reported:
        # The suite did not run at all -- an import-time explosion, not a bug
        # with a blast radius. Journal bug #12's lesson: a suite that cannot
        # report is not a suite that passed, and it is not one that failed
        # usefully either.
        log(f"    {mutant.summary}: suite did not run -- skipped")
        return None

    broke = sorted(after.failures - baseline.failures)
    if not broke:
        log(f"    {mutant.summary}: nothing failed -- line not covered")
        return None

    log(f"    {mutant.summary}: breaks {len(broke)} test(s)")
    return MutantTask(
        mid=mutant.mid, path=mutant.path, line=mutant.line,
        operator=mutant.operator, before=mutant.before, after=mutant.after,
        fail_to_pass=broke, issue=describe(mutant, broke, after.output),
    )


def save(tasks: list[MutantTask], path: Path) -> None:
    """Freeze the set so every later experiment runs the same bugs.

    Without this, E2 and E3 would each invent their own mutants and could not
    be compared with each other or re-run.
    """
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    Path(path).write_text(
        json.dumps([t.to_json() for t in tasks], indent=1), encoding="utf-8")


def load(path: Path) -> list[MutantTask]:
    raw = json.loads(Path(path).read_text(encoding="utf-8"))
    return [MutantTask(**r) for r in raw]


def commit_mutant(mutant: Mutant, repo: str, base: str, root) -> str:
    """Record the broken state as a real commit, and return its sha.

    Everything downstream -- the scout's worktree, each builder attempt, the
    grader's checkouts -- takes a repo and a commit. Making the mutation a
    commit means every one of those works unchanged: no special "and also
    apply this mutation" path threaded through the pipeline, which is exactly
    the sort of parallel code path that drifts out of step with the real one.

    The commit is kept alive by a ref under refs/mutants/, so it survives gc
    and can be checked out by hand when a result looks wrong.
    """
    from engine.repo.workspace import _git, attempt_worktree

    with attempt_worktree(repo, base, "mutcommit", 1, root) as tree:
        (Path(tree) / mutant.path).write_text(mutant.source, encoding="utf-8")
        if _git(["add", "-A"], cwd=tree).returncode != 0:
            raise WorkspaceError(f"{mutant.mid}: git add failed")
        made = _git(["-c", "user.name=The Garage",
                     "-c", "user.email=garage@localhost",
                     "commit", "-m",
                     f"mutant {mutant.mid}: {mutant.operator} at "
                     f"{mutant.path}:{mutant.line}"], cwd=tree)
        if made.returncode != 0:
            raise WorkspaceError(f"{mutant.mid}: commit failed: {made.stderr[-200:]}")
        sha = _git(["rev-parse", "HEAD"], cwd=tree).stdout.strip()
        # A ref, so gc cannot collect the only copy of a task we are measuring.
        _git(["update-ref", f"refs/mutants/{mutant.mid}", sha], cwd=tree)
    return sha


def mutant_grader(task, root, runner, log=print, progress=None):
    """Grade a mutant repair. Simpler than repo mode, and stricter.

    Repo mode has to manufacture its own evidence with a witness test, because
    nobody knows what "fixed" means for an arbitrary issue. A mutant needs no
    such thing: the suite was GREEN before the mutation (a red baseline is
    refused outright), so "fixed" means green again. Nothing else counts, and
    a patch that repairs the bug while breaking something else fails on the
    same check -- no separate regression pass needed.

    Note what this does NOT check: whether the model restored the original
    line. A different fix that makes the suite green is a pass, which is the
    honest position -- the tests are the specification. The original line is
    kept in the manifest so the two can be compared afterwards.
    """
    import time as _time

    from engine.eval.repo_grader import RepoGrade, run_suite
    from engine.repo.patch import apply_patch
    from engine.repo.workspace import attempt_worktree

    def grade(state, attempt, run_dir):
        started = _time.monotonic()
        if progress:
            progress("patched")
        n = int(attempt.get("n", 1))
        with attempt_worktree(task.repo, state["base_commit"], state["task_id"],
                              n * 10 + 1, root) as tree:
            applied = apply_patch(attempt.get("patch", ""), tree)
            if not applied.applied:
                raise GradingInfraError(
                    f"could not re-apply for grading: {applied.stderr[-300:]}")
            after = run_suite(tree, task.suite, runner)

        wall = int((_time.monotonic() - started) * 1000)
        tail = after.output[-4000:]
        if not after.reported:
            return RepoGrade("fail", False, "suite_broken", tail, wall,
                             regressions=["<suite did not run>"])
        if after.failures:
            return RepoGrade("fail", False, "still_failing", tail, wall,
                             regressions=sorted(after.failures)[:10])
        return RepoGrade("pass", True, "", tail, wall)

    grade.close = getattr(runner, "close", lambda: None)
    return grade


def scout_found_it(run_dir, task_id: str, path: str) -> bool | None:
    """Did the context pack contain the file that was actually broken?

    On a mutant this is a direct measurement rather than the inference a gold
    patch forces: we know exactly which file we broke. None when no pack was
    written (the scout was off).
    """
    import re

    pack = Path(run_dir) / "tasks" / task_id / "context_pack.md"
    if not pack.is_file():
        return None
    files = set(re.findall(r"^--- (\S+) \(lines", pack.read_text(encoding="utf-8"), re.M))
    return path in files


def restored_original(run_dir, task_id: str, before: str, after: str) -> bool | None:
    """Did the patch put the broken line back, or just quiet the tests?

    "Solved" means the suite went green, and that is not the same as repairing
    the mutation. On the first real mutant run one task went green by leaving
    the inverted condition exactly where it was and reimplementing the feature
    nine lines further down -- two implementations of one behaviour, one of
    them still broken, and the reviewer accepted it on the first attempt.

    Nobody would have known: the score said 7/9. It said 6/9 only because the
    original line was kept and someone went looking. Nothing that matters
    should depend on someone going looking, so it is reported alongside.

    None when there is no patch on disk to inspect.
    """
    patches = sorted(Path(run_dir).glob(f"tasks/{task_id}/attempts/*/patch.diff"))
    if not patches:
        return None
    diff = patches[-1].read_text(encoding="utf-8")
    added = [l[1:].strip() for l in diff.splitlines()
             if l.startswith("+") and not l.startswith("+++")]
    removed = [l[1:].strip() for l in diff.splitlines()
               if l.startswith("-") and not l.startswith("---")]
    return before.strip() in added and after.strip() in removed
