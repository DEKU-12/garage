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
