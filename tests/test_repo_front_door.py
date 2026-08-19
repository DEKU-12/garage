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
