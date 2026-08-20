"""Repo mode: detection, grading without an answer key, and shipping.

Build week: 6. Offline, no Docker, no network -- the suite runner is injected
(rules.md: everything must run under --model stub with nothing plugged in).

The most important test in this file is `test_a_patch_that_proves_nothing_is_
unverified`. An engine with no ground truth will report success for a change
that does nothing, and that is the single failure mode repo mode exists to
prevent.
"""

from __future__ import annotations

import subprocess
import time

import pytest

from engine.errors import WorkspaceError
from engine.eval.repo_grader import (
    RepoGrade,
    SuiteRun,
    grade_repo,
    is_test_path,
    parse_failures,
    split_test_hunks,
)
from engine.repo.detect import Suite, detect_suite
from engine.repo.front_door import parse_repo_url
from engine.repo.ship import (
    assert_not_protected,
    branch_name,
    commit_patch,
    pr_body,
)

PYTEST_SUITE = Suite(kind="pytest", image="python:3.11-slim", setup=[],
                     command=["pytest"], per_test=True)
OPAQUE_SUITE = Suite(kind="go", image="golang:1.22", setup=[],
                     command=["go", "test"], per_test=False)


# --------------------------------------------------------------- detection

def _touch(root, path, text="x"):
    p = root / path
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text)
    return p


def test_python_repo_with_tests_is_pytest(tmp_path):
    _touch(tmp_path, "pyproject.toml", "[project]\nname='x'\n")
    _touch(tmp_path, "tests/test_a.py", "def test_a(): pass\n")
    suite = detect_suite(tmp_path)
    assert suite is not None
    assert suite.kind == "pytest" and suite.per_test


def test_python_repo_without_tests_is_refused(tmp_path):
    """No tests means no way to check a fix. Refuse rather than guess."""
    _touch(tmp_path, "pyproject.toml", "[project]\nname='x'\n")
    assert detect_suite(tmp_path) is None


def test_npm_repo_is_detected_but_cannot_name_tests(tmp_path):
    _touch(tmp_path, "package.json", '{"scripts": {"test": "jest"}}')
    suite = detect_suite(tmp_path)
    assert suite is not None and suite.kind == "npm"
    # This is what caps such a repo at "unverified" forever.
    assert suite.per_test is False


def test_package_json_without_a_test_script_is_refused(tmp_path):
    _touch(tmp_path, "package.json", '{"name": "x"}')
    assert detect_suite(tmp_path) is None


def test_unknown_repo_is_refused(tmp_path):
    _touch(tmp_path, "README.md", "hello")
    assert detect_suite(tmp_path) is None


# ------------------------------------------------------------------- urls

@pytest.mark.parametrize("url", [
    "https://github.com/psf/requests",
    "https://github.com/psf/requests.git",
    "http://www.github.com/psf/requests/",
    "git@github.com:psf/requests.git",
    "github.com/psf/requests",       # what --url's own help text advertises
    "www.github.com/psf/requests",
    "psf/requests",
])
def test_urls_all_resolve_to_owner_name(url):
    assert parse_repo_url(url) == "psf/requests"


@pytest.mark.parametrize("bad", ["", "not a url", "https://gitlab.com/a/b", "psf"])
def test_bad_urls_are_refused(bad):
    with pytest.raises(WorkspaceError):
        parse_repo_url(bad)


# ------------------------------------------------------------ patch splitting

def test_test_paths_are_recognised():
    assert is_test_path("tests/test_thing.py")
    assert is_test_path("pkg/tests/test_thing.py")
    assert is_test_path("pkg/thing_test.go")
    assert is_test_path("conftest.py")
    assert not is_test_path("engine/thing.py")
    assert not is_test_path("src/latest.py")   # 'test' inside a word is not a test


def test_split_separates_the_witness_from_the_fix():
    diff = (
        "diff --git a/src/app.py b/src/app.py\n"
        "--- a/src/app.py\n+++ b/src/app.py\n@@ -1 +1 @@\n-old\n+new\n"
        "diff --git a/tests/test_app.py b/tests/test_app.py\n"
        "--- a/tests/test_app.py\n+++ b/tests/test_app.py\n@@ -1 +1 @@\n-a\n+b\n"
    )
    tests, src = split_test_hunks(diff)
    assert "tests/test_app.py" in tests and "src/app.py" not in tests
    assert "src/app.py" in src and "tests/test_app.py" not in src


def test_parse_failures_reads_pytest_and_ignores_opaque_suites():
    out = "FAILED tests/test_a.py::test_x - AssertionError\nERROR tests/test_b.py::test_y\n"
    assert parse_failures(out, PYTEST_SUITE) == {
        "tests/test_a.py::test_x", "tests/test_b.py::test_y"}
    assert parse_failures(out, OPAQUE_SUITE) == frozenset()


# ---------------------------------------------------------------- grading

def _grade(baseline, patched, witness_before=None, witness_tests=(), suite=PYTEST_SUITE):
    return grade_repo(suite=suite, baseline=baseline, patched=patched,
                      witness_before=witness_before,
                      witness_tests=list(witness_tests), started=time.monotonic())


def test_a_real_fix_with_a_witness_passes():
    """Known-GOOD input: the witness failed before and passes after."""
    g = _grade(
        baseline=SuiteRun(1, "", frozenset({"tests/test_a.py::test_x"})),
        patched=SuiteRun(0, "", frozenset()),
        witness_before=SuiteRun(1, "", frozenset({"tests/test_a.py::test_x"})),
        witness_tests=["tests/test_a.py::test_x"],
    )
    assert g.verdict == "pass" and g.resolved


def test_a_patch_that_proves_nothing_is_unverified():
    """Known-BAD input, and the whole reason this module exists.

    The suite was green and is still green. Nothing regressed -- and nothing
    was demonstrated either. Reporting this as a fix would be a lie told to
    someone who is asleep.
    """
    g = _grade(baseline=SuiteRun(0, "", frozenset()),
               patched=SuiteRun(0, "", frozenset()))
    assert g.verdict == "unverified"
    assert g.resolved is False
    assert g.reason == "no_witness_test"


def test_a_regression_is_a_failure_not_an_unverified():
    g = _grade(baseline=SuiteRun(0, "", frozenset()),
               patched=SuiteRun(1, "", frozenset({"tests/test_b.py::test_y"})))
    assert g.verdict == "fail" and g.regressions == ["tests/test_b.py::test_y"]


def test_pre_existing_failures_are_not_counted_as_regressions():
    """A repo that is already red stays gradeable: only NEW failures count."""
    already = frozenset({"tests/test_old.py::test_broken"})
    g = _grade(baseline=SuiteRun(1, "", already),
               patched=SuiteRun(1, "", already),
               witness_before=SuiteRun(1, "", already | {"tests/test_a.py::test_x"}),
               witness_tests=["tests/test_a.py::test_x"])
    assert g.verdict == "pass", g.reason


def test_a_witness_that_already_passed_proves_nothing():
    g = _grade(baseline=SuiteRun(0, "", frozenset()),
               patched=SuiteRun(0, "", frozenset()),
               witness_before=SuiteRun(0, "", frozenset()),   # it did not fail
               witness_tests=["tests/test_a.py::test_x"])
    assert g.verdict == "unverified" and g.reason == "witness_passed_before"


def test_a_witness_still_failing_after_the_fix_is_a_failure():
    g = _grade(baseline=SuiteRun(1, "", frozenset({"tests/test_a.py::test_x"})),
               patched=SuiteRun(1, "", frozenset({"tests/test_a.py::test_x"})),
               witness_before=SuiteRun(1, "", frozenset({"tests/test_a.py::test_x"})),
               witness_tests=["tests/test_a.py::test_x"])
    assert g.verdict == "fail" and g.reason == "witness_still_failing"


def test_a_suite_that_never_ran_is_a_failure_not_a_clean_bill():
    """The one that got away in testing: a patch broke the package so badly
    that `pip install -e .` failed and pytest never started. Zero failures were
    parsed, and zero failures looked exactly like a clean run."""
    broken = SuiteRun(1, "ERROR: Failed to build 'file:///repo'", frozenset(),
                      reported=False)
    g = _grade(baseline=SuiteRun(0, "42 passed", frozenset()), patched=broken)
    assert g.verdict == "fail"
    assert g.reason == "suite_broken"
    assert g.resolved is False


def test_suite_reported_detects_a_real_pytest_run():
    from engine.eval.repo_grader import suite_reported
    assert suite_reported("42 passed in 1.2s", PYTEST_SUITE)
    assert suite_reported("1 failed, 41 passed", PYTEST_SUITE)
    assert suite_reported("no tests ran in 0.1s", PYTEST_SUITE)
    assert not suite_reported("ERROR: Failed to build 'file:///repo'", PYTEST_SUITE)
    assert not suite_reported("", PYTEST_SUITE)
    # an opaque suite has only its exit code, so it is always "reported"
    assert suite_reported("", OPAQUE_SUITE)


def test_a_suite_that_cannot_name_tests_tops_out_at_unverified():
    g = _grade(baseline=SuiteRun(0, "", frozenset()),
               patched=SuiteRun(0, "", frozenset()), suite=OPAQUE_SUITE)
    assert g.verdict == "unverified" and g.reason == "no_per_test_reporting"


def test_an_opaque_suite_going_green_to_red_is_still_a_regression():
    g = _grade(baseline=SuiteRun(0, "", frozenset()),
               patched=SuiteRun(1, "", frozenset()), suite=OPAQUE_SUITE)
    assert g.verdict == "fail" and g.reason == "regressions"


def test_unverified_is_never_resolved():
    """Belt and braces: no path may set resolved=True without a pass."""
    for g in [
        _grade(SuiteRun(0, "", frozenset()), SuiteRun(0, "", frozenset())),
        _grade(SuiteRun(0, "", frozenset()), SuiteRun(0, "", frozenset()),
               suite=OPAQUE_SUITE),
    ]:
        assert isinstance(g, RepoGrade)
        assert (g.verdict == "pass") == g.resolved


# ---------------------------------------------------------------- shipping

def test_branch_name_says_what_it_is():
    assert branch_name("psf__requests-abc1234", "pass").startswith("garage/fix-")
    assert branch_name("psf__requests-abc1234", "unverified").startswith(
        "garage/unverified-")


@pytest.mark.parametrize("branch", ["main", "master", "trunk", "develop"])
def test_protected_branches_are_refused(branch):
    with pytest.raises(WorkspaceError):
        assert_not_protected(branch, "main")


def test_the_repos_own_default_branch_is_refused_even_if_oddly_named():
    with pytest.raises(WorkspaceError):
        assert_not_protected("release", "release")


def _git_repo(tmp_path):
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    for k, v in (("user.email", "t@t"), ("user.name", "t")):
        subprocess.run(["git", "-C", str(tmp_path), "config", k, v], check=True)
    (tmp_path / "app.py").write_text("old\n")
    subprocess.run(["git", "-C", str(tmp_path), "add", "-A"], check=True)
    subprocess.run(["git", "-C", str(tmp_path), "commit", "-qm", "base"], check=True)
    return tmp_path


def test_commit_lands_on_a_new_branch_and_never_on_the_default(tmp_path):
    tree = _git_repo(tmp_path)
    (tree / "app.py").write_text("new\n")
    res = commit_patch(tree, "garage/fix-x", "main", "fix: a thing")
    assert res.branch == "garage/fix-x" and res.commit and not res.pushed
    head = subprocess.run(["git", "-C", str(tree), "rev-parse", "--abbrev-ref", "HEAD"],
                          capture_output=True, text=True, check=True)
    assert head.stdout.strip() == "garage/fix-x"


def test_an_empty_change_is_refused_rather_than_committed(tmp_path):
    """Journal bug #11: a commit message claiming a change the diff never made."""
    tree = _git_repo(tmp_path)
    with pytest.raises(WorkspaceError, match="empty change"):
        commit_patch(tree, "garage/fix-x", "main", "fix: nothing at all")


def test_pr_body_states_plainly_when_nothing_was_proved():
    body = pr_body(None, "unverified", "no_witness_test", [], [], 3)
    assert "no witness test" in body.lower()
    assert "not been shown to fix anything" in body.lower()
    assert "unverified" in body.lower()


def test_pr_body_names_the_witness_when_there_is_one():
    body = pr_body(None, "pass", "", ["tests/test_a.py::test_x"], [], 2)
    assert "tests/test_a.py::test_x" in body


# ------------------------------------------- wider detection + refusals

def test_a_makefile_test_target_is_a_last_resort_suite(tmp_path):
    _touch(tmp_path, "Makefile", "build:\n\tgcc x.c\ntest:\n\t./run_tests.sh\n")
    suite = detect_suite(tmp_path)
    assert suite is not None and suite.kind == "make"
    assert suite.per_test is False      # so it can never claim a confirmed fix


def test_a_makefile_without_a_test_target_is_not_a_suite(tmp_path):
    _touch(tmp_path, "Makefile", "build:\n\tgcc x.c\n")
    assert detect_suite(tmp_path) is None


def test_dev_requirements_are_installed_too(tmp_path):
    _touch(tmp_path, "pyproject.toml", "[project]\nname='x'\n")
    _touch(tmp_path, "requirements-dev.txt", "pytest-mock\n")
    _touch(tmp_path, "tests/test_a.py", "def test_a(): pass\n")
    flat = [" ".join(step) for step in detect_suite(tmp_path).setup]
    assert any("requirements-dev.txt" in s for s in flat)


def test_tox_and_pytest_ini_count_as_python_markers(tmp_path):
    _touch(tmp_path, "tox.ini", "[tox]\n")
    _touch(tmp_path, "tests/test_a.py", "def test_a(): pass\n")
    assert detect_suite(tmp_path).kind == "pytest"


def test_a_compose_repo_is_refused_and_says_why(tmp_path):
    """A suite needing a database standing up alongside it cannot be run by
    dropping a container over a checkout -- and the refusal should say so."""
    from engine.repo.detect import refusal_reason
    _touch(tmp_path, "docker-compose.yml", "services:\n  db:\n    image: postgres\n")
    _touch(tmp_path, "pyproject.toml", "[project]\nname='x'\n")
    assert detect_suite(tmp_path) is None
    assert "other services" in refusal_reason(tmp_path)


def test_a_python_repo_with_no_tests_says_that_specifically(tmp_path):
    from engine.repo.detect import refusal_reason
    _touch(tmp_path, "pyproject.toml", "[project]\nname='x'\n")
    assert "could not find any tests" in refusal_reason(tmp_path)


# ------------------------------------------------- network isolation

class _FakeExec:
    """Records docker invocations so the two-phase split can be asserted."""

    def __init__(self, fail_commit=False):
        self.calls: list[list[str]] = []
        self.fail_commit = fail_commit

    def __call__(self, argv, **kw):
        self.calls.append(argv)
        import subprocess as sp
        rc = 1 if (self.fail_commit and argv[1] == "commit") else 0
        return sp.CompletedProcess(argv, rc, stdout="42 passed", stderr="")

    def runs(self):
        return [c for c in self.calls if len(c) > 1 and c[1] == "run"]


def test_tests_run_with_the_network_off(tmp_path):
    """Installing needs the network. Running somebody else's test suite does
    not -- and that is the step that executes untrusted code."""
    from engine.eval.repo_grader import docker_runner
    ex = _FakeExec()
    docker_runner("python:3.11-slim", tmp_path,
                  [["pip", "install", "-e", "."], ["pytest"]], exec_=ex)
    setup_run, test_run = ex.runs()
    assert "--network" not in setup_run, "setup must keep the network"
    assert "--network" in test_run and "none" in test_run
    assert any(c[1] == "commit" for c in ex.calls), "prepared image never committed"


def test_a_suite_with_no_setup_is_offline_from_the_start(tmp_path):
    from engine.eval.repo_grader import docker_runner
    ex = _FakeExec()
    docker_runner("golang:1.22", tmp_path, [["go", "test", "./..."]], exec_=ex)
    (only,) = ex.runs()
    assert "--network" in only and "none" in only


def test_a_failed_commit_downgrades_loudly_rather_than_silently(tmp_path):
    from engine.eval.repo_grader import docker_runner
    ex = _FakeExec(fail_commit=True)
    _code, out = docker_runner("python:3.11-slim", tmp_path,
                               [["pip", "install", "-e", "."], ["pytest"]], exec_=ex)
    assert "NOT network-isolated" in out


def test_the_throwaway_container_and_image_are_always_cleaned_up(tmp_path):
    from engine.eval.repo_grader import docker_runner
    ex = _FakeExec()
    docker_runner("python:3.11-slim", tmp_path,
                  [["pip", "install", "-e", "."], ["pytest"]], exec_=ex)
    assert any(c[1] == "rm" for c in ex.calls)
    assert any(c[1] == "rmi" for c in ex.calls)


# ------------------------------------------------- install once, test often

def test_the_environment_is_prepared_once_and_reused(tmp_path):
    """Three suite runs per attempt were three full dependency installs.

    Measured on NL2SQL: 1034 of a run's 1067 seconds were Docker, nearly all
    of it the same `pip install` repeated. The model used about thirty
    seconds. This is the difference between a run you supervise and one you
    fire off.
    """
    from engine.eval.repo_grader import CachedImage

    ex = _FakeExec()
    cache = CachedImage("abc123", exec_=ex)
    steps = [["pip", "install", "-e", "."], ["pytest"]]
    for _ in range(3):                       # baseline, patched, witness
        cache("python:3.11-slim", tmp_path, steps)

    # Match on the shell script docker is handed (the last argument), not the
    # whole command: pytest's own tmp_path lives under `pytest-of-<user>/`, so
    # matching the joined command counted the mount path as a test run.
    script = lambda c: c[-1]
    installs = [c for c in ex.calls if c[1] == "run" and "pip" in script(c)]
    commits = [c for c in ex.calls if c[1] == "commit"]
    assert len(installs) == 1, f"installed {len(installs)} times, expected once"
    assert len(commits) == 1

    test_runs = [c for c in ex.runs() if script(c).strip() == "pytest"]
    assert len(test_runs) == 3
    for r in test_runs:
        assert "--network" in r and "none" in r      # still isolated
    assert all("garage-prepared:abc123" in " ".join(r) for r in test_runs[1:])


def test_the_prepared_image_is_deleted_when_the_run_ends(tmp_path):
    """A cache, not an artifact: it must not outlive the run that built it."""
    from engine.eval.repo_grader import CachedImage

    ex = _FakeExec()
    cache = CachedImage("abc123", exec_=ex)
    cache("python:3.11-slim", tmp_path, [["pip", "install", "."], ["pytest"]])
    cache.close()
    assert any(c[1] == "rmi" for c in ex.calls)


def test_a_different_commit_does_not_inherit_another_run_s_dependencies(tmp_path):
    from engine.eval.repo_grader import CachedImage

    a, b = CachedImage("commit-a"), CachedImage("commit-b")
    assert a.tag != b.tag


# ------------------------------------------------------- branch collisions

def _tiny_repo(tmp_path):
    import subprocess
    r = tmp_path / "repo"
    r.mkdir()
    run = lambda *a: subprocess.run(["git", *a], cwd=r, capture_output=True, check=True)
    run("init", "-q", "-b", "main")
    (r / "a.txt").write_text("hello\n")
    run("add", "-A")
    run("-c", "user.email=t@t", "-c", "user.name=t", "commit", "-qm", "first")
    return r


def test_a_second_run_on_the_same_commit_gets_its_own_branch(tmp_path):
    """The branch name is derived from repo + commit, so running twice against
    the same commit produced the same name and `checkout -b` failed.

    Reusing the branch would be worse than failing: it would quietly bury an
    earlier attempt somebody may not have merged yet.
    """
    from engine.repo.ship import branch_exists, commit_patch, unique_branch

    repo = _tiny_repo(tmp_path)
    name = "garage/fix-thing-abc1234"

    assert unique_branch(repo, name) == name          # nothing there yet
    (repo / "b.txt").write_text("one\n")
    commit_patch(repo, name, "main", "first change")
    assert branch_exists(repo, name)

    second = unique_branch(repo, name)
    assert second == name + "-2" and not branch_exists(repo, second)


def test_a_failed_branch_creation_reports_gits_own_words(tmp_path):
    """"could not create branch X" could not be told apart from a bad name or
    a detached HEAD. git already knows which it is."""
    from engine.errors import WorkspaceError
    from engine.repo.ship import commit_patch

    repo = _tiny_repo(tmp_path)
    (repo / "b.txt").write_text("one\n")
    commit_patch(repo, "garage/taken", "main", "first")
    (repo / "c.txt").write_text("two\n")
    with pytest.raises(WorkspaceError) as exc:
        commit_patch(repo, "garage/taken", "main", "again")
    assert "already exists" in str(exc.value)


# --------------------------------------------- the container the repo needs

def test_the_interpreter_follows_requires_python(tmp_path):
    """A repo pinning ==3.12.* cannot be installed by 3.11: pip refuses.

    Combined with a tolerant `|| true` that produced a container with NONE of
    the project's dependencies, and a suite failing with ModuleNotFoundError
    -- which reads as a broken repo rather than a wrong image. It cost two
    wrong diagnoses before anyone looked at the actual error.
    """
    from engine.repo.detect import python_image

    _touch(tmp_path, "pyproject.toml", '[project]\nrequires-python = "==3.12.*"\n')
    assert python_image(tmp_path) == "python:3.12-slim"

    _touch(tmp_path, "pyproject.toml", '[project]\nrequires-python = ">=3.10"\n')
    assert python_image(tmp_path) == "python:3.10-slim"


def test_an_unparseable_or_absent_pin_falls_back(tmp_path):
    """Guessing an interpreter from a constraint we cannot read is worse than
    using the default and letting the suite say so."""
    from engine.repo.detect import python_image

    assert python_image(tmp_path) == "python:3.11-slim"          # no pyproject
    _touch(tmp_path, "pyproject.toml", "[project]\nname='x'\n")
    assert python_image(tmp_path) == "python:3.11-slim"          # no pin
    _touch(tmp_path, "pyproject.toml", '[project]\nrequires-python = ">=2.7"\n')
    assert python_image(tmp_path) == "python:3.11-slim"          # out of range


def test_test_only_dependencies_are_installed(tmp_path):
    """pip install -e . installs neither PEP 735 groups nor unnamed extras."""
    from engine.repo.detect import test_requirements

    _touch(tmp_path, "pyproject.toml",
           '[project]\nname="x"\n'
           '[project.optional-dependencies]\ntest = ["responses"]\n'
           '[dependency-groups]\ndev = ["httpx>=0.28.1", "pytest"]\n'
           'lint = ["ruff"]\n')
    got = test_requirements(tmp_path)
    assert "httpx>=0.28.1" in got and "responses" in got
    assert "ruff" not in got, "lint deps are not test deps"


def test_a_timed_out_container_is_force_removed(tmp_path):
    """A timeout kills the docker CLIENT, not the container.

    `--rm` cleans up on normal exit only, so a hung suite kept running and
    kept burning CPU. During one generation run those orphans accumulated
    until every later candidate competed with them and the run crawled from
    39 seconds per mutant to 465.
    """
    import subprocess

    from engine.errors import GradingInfraError
    from engine.eval.repo_grader import docker_runner

    calls = []

    def timing_out(argv, **kw):
        calls.append(argv)
        if argv[1] == "run":
            raise subprocess.TimeoutExpired(argv, kw.get("timeout", 1))
        return subprocess.CompletedProcess(argv, 0, stdout="", stderr="")

    with pytest.raises(GradingInfraError):
        docker_runner("python:3.12-slim", tmp_path, [["pytest"]], exec_=timing_out)

    removed = [c for c in calls if c[1:3] == ["rm", "-f"]]
    assert removed, "the container was left running after the timeout"
    named = [c for c in calls if c[1] == "run" and "--name" in c]
    assert named, "cannot force-remove a container that was never named"


def test_containers_run_on_the_native_platform(tmp_path):
    """Forcing --platform linux/amd64 ran everything under Rosetta on Apple
    Silicon. That flag exists for SWE-bench's x86-only images, which are
    graded by a different path entirely; these are ordinary python:3.x
    images with native builds."""
    from engine.eval.repo_grader import docker_runner

    ex = _FakeExec()
    docker_runner("python:3.12-slim", tmp_path, [["pytest"]], exec_=ex)
    for call in ex.runs():
        assert "--platform" not in call
