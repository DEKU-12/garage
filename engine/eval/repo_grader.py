"""Grading a fix to a repo that ships no answer key (repo front door).

Build week: 6.

A SWE-bench task tells you exactly which tests must flip from failing to
passing. Your own repo tells you nothing. That single missing list is the whole
difficulty of repo mode: without it, "did it work?" has no ground truth, and an
engine with no ground truth will happily report success for a patch that
changed nothing.

So a fix has to earn the word "fixed" two ways at once:

  1. **No regressions.** Every test that passed before the patch still passes.
  2. **A witness test.** The patch must add or change a test that FAILS on the
     unpatched tree and PASSES on the patched one. That is the only available
     evidence that the change does something, and that the something is the
     thing that was asked for.

If there are no regressions but no witness, the outcome is **`unverified`** --
never `pass`. Unverified is a first-class result that is reported as itself and
never rounded up to a fix (rules.md §0). It is the difference between "I
changed some code" and "I fixed your bug", and an overnight agent that blurs
those two is worse than no agent.

Suites that cannot name their failing tests (npm, go, cargo -- see
engine/repo/detect.py) can still be checked for regressions by exit code, but
can never produce a witness, so they top out at `unverified` by construction.
That is a real limitation, stated rather than papered over.

Untrusted repo code executes ONLY inside Docker (NFR-3, rules.md §4.1.5). The
`runner` argument exists so the tests for this module can drive it without
Docker and without a network -- it is never a way to run a checkout on the host.

Network: dependency installation genuinely needs the network, but the test run
does not -- and the test run is the part that executes the repo's own code. So
they are split: setup runs in a container with the network, that container is
committed to a throwaway image, and the tests run from that image with
`--network none`. A repo's test suite therefore cannot phone home, exfiltrate,
or fetch anything at the moment it is running.

If the commit step fails for any reason the run falls back to a single online
container and says so in the captured output, because silently downgrading an
isolation guarantee is worse than not having it.

Emits: nothing directly -- the tester node emits around it.
"""

from __future__ import annotations

import re
import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path
from uuid import uuid4

from engine.errors import GradingInfraError
from engine.repo.detect import Suite

DOCKER_TIMEOUT_S = 1800
LOG_TAIL_CHARS = 4000

# "FAILED tests/test_x.py::test_y - AssertionError" and the ERROR equivalent.
_PYTEST_FAIL = re.compile(r"^(?:FAILED|ERROR)\s+(\S+?)(?:\s|$)", re.M)

# Proof that pytest actually got as far as reporting on tests. Without one of
# these the run told us nothing, and "nothing" is not "nothing wrong".
_PYTEST_RAN = re.compile(r"\b\d+ (?:passed|failed|error|skipped)|no tests ran", re.I)


@dataclass(frozen=True)
class SuiteRun:
    exit_code: int
    output: str
    failures: frozenset[str]        # empty when the suite cannot name them
    reported: bool = True           # did the suite get far enough to report?


@dataclass(frozen=True)
class RepoGrade:
    """The verdict on one patch against a repo with no answer key."""

    verdict: str                    # "pass" | "fail" | "unverified"
    resolved: bool                  # only ever True for "pass"
    reason: str                     # machine-readable why
    log_tail: str
    wall_ms: int
    regressions: list[str] = field(default_factory=list)
    witness_tests: list[str] = field(default_factory=list)


# --------------------------------------------------------------- the patch

_TEST_PATH = re.compile(r"(^|/)(tests?|testing)/|(^|/)test_[^/]+$|_test\.[a-z]+$"
                        r"|(^|/)conftest\.py$")


def is_test_path(path: str) -> bool:
    """Does this file hold tests? Path-shape only -- we never read the file."""
    return bool(_TEST_PATH.search(path.strip()))


def split_test_hunks(diff: str) -> tuple[str, str]:
    """Split a diff into (tests-only, everything-else).

    The witness check needs to apply the test half WITHOUT the fix, to prove
    the new test actually fails on the broken code. A test that passes before
    the fix witnesses nothing.
    """
    test_parts: list[str] = []
    src_parts: list[str] = []
    current: list[str] | None = None
    for line in diff.splitlines(keepends=True):
        if line.startswith("diff --git "):
            bits = line.split()
            path = bits[-1][2:] if len(bits) >= 4 else ""
            current = test_parts if is_test_path(path) else src_parts
        if current is not None:
            current.append(line)
    return "".join(test_parts), "".join(src_parts)


def parse_failures(output: str, suite: Suite) -> frozenset[str]:
    """Which tests failed, when the suite is capable of saying."""
    if not suite.per_test:
        return frozenset()
    return frozenset(_PYTEST_FAIL.findall(output))


# --------------------------------------------------------------- execution

def _sh(argv_list: list[list[str]]) -> str:
    return " && ".join(" ".join(_sh_quote(a) for a in argv) for argv in argv_list)


def _docker(args: list[str], timeout_s: int, exec_=subprocess.run) -> tuple[int, str]:
    try:
        proc = exec_(["docker", *args], capture_output=True, text=True,
                     timeout=timeout_s, check=False)
    except subprocess.TimeoutExpired as exc:
        raise GradingInfraError(f"docker timed out after {timeout_s}s") from exc
    except FileNotFoundError as exc:
        raise GradingInfraError("docker not found -- repo mode requires Docker") from exc
    return proc.returncode, (proc.stdout or "") + (proc.stderr or "")


def docker_runner(image: str, tree: Path, argv_list: list[list[str]],
                  timeout_s: int = DOCKER_TIMEOUT_S, exec_=subprocess.run
                  ) -> tuple[int, str]:
    """Install with the network, then run the tests without it.

    The default runner, and the only one that ever touches real repo code.

    Splitting the two phases is the whole point: installing dependencies needs
    the network, running somebody else's test suite does not -- and the test
    run is precisely the step that executes untrusted code.
    """
    mount = ["-v", f"{Path(tree).resolve()}:/repo", "-w", "/repo",
             "--platform", "linux/amd64"]
    setup, test = argv_list[:-1], argv_list[-1]

    if not setup:                       # nothing to install: straight to jail
        return _docker(["run", "--rm", "--network", "none", *mount,
                        image, "sh", "-lc", _sh([test])], timeout_s, exec_)

    token = uuid4().hex[:12]
    cid, tag = f"garage-setup-{token}", f"garage-prepared:{token}"
    try:
        code, out = _docker(["run", "--name", cid, *mount,
                             image, "sh", "-lc", _sh(setup)], timeout_s, exec_)
        if code != 0:
            # Setup failing IS the answer -- the caller decides what it means.
            return code, out

        commit_code, commit_out = _docker(["commit", cid, tag], 300, exec_)
        if commit_code != 0:
            return _fallback(mount, image, argv_list, timeout_s, exec_, commit_out)

        code2, out2 = _docker(["run", "--rm", "--network", "none", *mount,
                               tag, "sh", "-lc", _sh([test])], timeout_s, exec_)
        return code2, out + "\n[garage] tests ran with --network none\n" + out2
    finally:
        _docker(["rm", "-f", cid], 120, exec_)
        _docker(["rmi", "-f", tag], 120, exec_)


def _fallback(mount, image, argv_list, timeout_s, exec_, why: str) -> tuple[int, str]:
    """One online container. Loudly, because an isolation guarantee that
    quietly stops holding is worse than one that was never claimed."""
    code, out = _docker(["run", "--rm", *mount, image, "sh", "-lc",
                         _sh(argv_list)], timeout_s, exec_)
    return code, ("[garage] WARNING: could not commit the prepared image, so the "
                  "test run was NOT network-isolated.\n" + why[-300:] + "\n" + out)


def _sh_quote(arg: str) -> str:
    return arg if re.fullmatch(r"[\w./:=-]+", arg) else "'" + arg.replace("'", "'\\''") + "'"


def suite_reported(output: str, suite: Suite) -> bool:
    """Did the suite actually run tests, or did it fall over first?

    This exists because of a real miss: a patch that broke the package at
    import time made `pip install -e .` fail, so pytest never ran, so the
    output contained ZERO failures -- and zero failures read as "nothing
    regressed". The suite exploding and the suite being clean are the same
    number of failed tests, and they must never be the same verdict.
    """
    if not suite.per_test:
        return True                 # exit code is all we ever had
    return bool(_PYTEST_RAN.search(output))


def run_suite(tree: Path, suite: Suite, runner=docker_runner) -> SuiteRun:
    code, out = runner(suite.image, tree, [*suite.setup, suite.command])
    return SuiteRun(code, out, parse_failures(out, suite), suite_reported(out, suite))


# ----------------------------------------------------------------- grading

def grade_repo(
    *,
    suite: Suite,
    baseline: SuiteRun,
    patched: SuiteRun,
    witness_before: SuiteRun | None,
    witness_tests: list[str],
    started: float,
) -> RepoGrade:
    """Turn three suite runs into one honest verdict.

    Pure: takes results, returns a verdict. Every branch here is a test case.
    """
    wall = int((time.monotonic() - started) * 1000)
    tail = patched.output[-LOG_TAIL_CHARS:]

    # Checked BEFORE anything is counted. A suite that never reported cannot
    # be mined for regressions -- and reading its empty failure list as "clean"
    # is how a patch that breaks the build gets called harmless.
    if not patched.reported:
        return RepoGrade("fail", False, "suite_broken", tail, wall,
                         regressions=["<suite did not run>"])

    if suite.per_test:
        regressions = sorted(patched.failures - baseline.failures)
    else:
        # No per-test list: the only regression we can see is green going red.
        regressions = (["<suite>"] if baseline.exit_code == 0 and patched.exit_code != 0
                       else [])

    if regressions:
        return RepoGrade("fail", False, "regressions", tail, wall,
                         regressions=regressions)

    if not suite.per_test:
        return RepoGrade("unverified", False, "no_per_test_reporting", tail, wall)

    if not witness_tests:
        return RepoGrade("unverified", False, "no_witness_test", tail, wall)

    # The witness must have FAILED on the unpatched tree, or it proves nothing.
    if witness_before is None:
        return RepoGrade("unverified", False, "witness_not_checked", tail, wall,
                         witness_tests=witness_tests)
    proved = [t for t in witness_tests if t in witness_before.failures]
    if not proved:
        return RepoGrade("unverified", False, "witness_passed_before", tail, wall,
                         witness_tests=witness_tests)

    # ...and must PASS now.
    still_failing = [t for t in proved if t in patched.failures]
    if still_failing:
        return RepoGrade("fail", False, "witness_still_failing", tail, wall,
                         witness_tests=still_failing)

    return RepoGrade("pass", True, "", tail, wall, witness_tests=proved)


# ------------------------------------------------------- the grader closure

def attempt_grader(task, root: Path, log=print, runner=docker_runner):
    """Build the grader the tester node calls, for one repo task.

    Three suite runs are involved and only two are per-attempt: the baseline is
    computed once and cached, because the unpatched tree does not change.

    Each run gets a **fresh worktree** off the shared bare clone -- the same
    guarantee the benchmark path has, and the reason attempt N+1 can never
    inherit attempt N's half-applied diff.
    """
    from engine.repo.patch import apply_patch, diff_paths
    from engine.repo.workspace import attempt_worktree

    suite = task.suite
    cached_baseline: list[SuiteRun] = []

    def _run_at(tag: int, patch: str = "") -> SuiteRun:
        with attempt_worktree(task.repo, task.base_commit, task.task_id,
                              tag, root) as tree:
            if patch:
                applied = apply_patch(patch, tree)
                if not applied.applied:
                    raise GradingInfraError(
                        "could not re-apply the patch for grading: "
                        f"{applied.stderr[-400:]}")
            return run_suite(tree, suite, runner)

    def baseline() -> SuiteRun:
        if not cached_baseline:
            log("  baseline: running the repo's own suite on the unpatched tree")
            cached_baseline.append(_run_at(0))
            b = cached_baseline[0]
            if not b.reported:
                # The repo's own suite does not run on an untouched checkout.
                # Nothing can be graded against that, and it is our problem,
                # not the model's (rules.md §3.1).
                raise GradingInfraError(
                    "the repo's test suite does not run on the unpatched tree, "
                    "so there is no baseline to compare against:\n"
                    + b.output[-800:])
            if suite.per_test and b.failures:
                # Recorded, not fatal. "No regressions" is only meaningful
                # against a known starting point, including a red one.
                log(f"  baseline: {len(b.failures)} test(s) already failing")
        return cached_baseline[0]

    def grade(state, attempt, run_dir):
        started = time.monotonic()
        base = baseline()
        n = int(attempt.get("n", 1))
        patch = attempt.get("patch", "")

        patched = _run_at(n * 10 + 1, patch)

        test_patch, _src = split_test_hunks(patch)
        witness_before: SuiteRun | None = None
        witness_tests: list[str] = []
        if test_patch.strip() and suite.per_test:
            touched = set(diff_paths(test_patch))
            # The test half WITHOUT the fix: a witness that already passes here
            # proves nothing about the change.
            witness_before = _run_at(n * 10 + 2, test_patch)
            witness_tests = sorted(
                t for t in witness_before.failures
                if t.split("::")[0].lstrip("./") in
                {p.lstrip("./") for p in touched}
            )
            log(f"  witness: {len(witness_tests)} test(s) fail before the fix")

        return grade_repo(suite=suite, baseline=base, patched=patched,
                          witness_before=witness_before,
                          witness_tests=witness_tests, started=started)

    return grade
