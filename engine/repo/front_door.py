"""Turn a GitHub URL into something the engine can actually work on.

Build week: 6.

This is the front door of the product half: a user points at their own repo
instead of at a benchmark instance. Everything downstream (scout, builder,
reviewer) is unchanged -- what changes is where the task comes from and how the
result is judged (engine/eval/repo_grader.py).

Two refusals are deliberate and load-bearing:

  * an unrecognised test suite is a **refusal**, not a guess. A run that cannot
    tell whether the tests passed cannot tell you anything.
  * a repo whose suite is already red at the base commit is accepted, but the
    pre-existing failures are recorded, because "no regressions" only means
    anything against a known starting point.

Nothing here executes repo code. Cloning and reading file names is inert; the
suite runs in Docker, later, in the grader.

Emits: nothing -- the CLI emits task_started around it.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from engine.errors import WorkspaceError
from engine.repo.detect import Suite, detect_suite
from engine.repo.workspace import _git, ensure_bare_clone

# Accepts every form a person actually types, including the bare
# "github.com/owner/name" that the CLI's own --url help advertises.
_URL = re.compile(
    r"^(?:(?:https?://)?(?:www\.)?github\.com/|git@github\.com:)?"
    r"(?P<owner>[\w.-]+)/(?P<name>[\w.-]+?)(?:\.git)?/?$"
)


@dataclass(frozen=True)
class RepoTask:
    """One repair job on a real repo. Mirrors eval.swebench_io.Task's shape
    where the graph needs it, minus the thing that does not exist: a
    fail_to_pass list."""

    task_id: str
    issue: str
    repo: str                 # owner/name
    base_commit: str
    default_branch: str
    suite: Suite
    image: str = ""           # unused in repo mode; the suite carries its own

    # The two things a real repo does NOT have, kept as empty fields so the
    # shared graph can consume a RepoTask without special-casing. Their
    # emptiness is the point: no answer key, and no known-correct fix to
    # replay. Anything that reads them must cope with nothing being there.
    fail_to_pass: tuple[str, ...] = ()
    gold_patch: str = ""


def parse_repo_url(url: str) -> str:
    """'https://github.com/psf/requests.git' -> 'psf/requests'."""
    m = _URL.match((url or "").strip())
    if not m:
        raise WorkspaceError(
            f"{url!r} is not a GitHub repo URL. Expected github.com/owner/name."
        )
    return f"{m['owner']}/{m['name']}"


def default_branch(bare: Path) -> str:
    """Whatever the remote calls its trunk. Never assumed to be 'main'."""
    proc = _git(["symbolic-ref", "--short", "HEAD"], cwd=bare)
    if proc.returncode == 0 and proc.stdout.strip():
        return proc.stdout.strip()
    for guess in ("main", "master"):
        if _git(["rev-parse", "--verify", guess], cwd=bare).returncode == 0:
            return guess
    raise WorkspaceError(f"{bare.name}: cannot work out the default branch")


def head_commit(bare: Path, branch: str) -> str:
    proc = _git(["rev-parse", branch], cwd=bare)
    if proc.returncode != 0:
        raise WorkspaceError(f"{bare.name}: cannot resolve {branch}")
    return proc.stdout.strip()


def open_repo(url: str, issue: str, root: Path,
              checkout: Path | None = None) -> RepoTask:
    """Clone (or reuse) the repo and work out how to test it.

    `checkout` lets a caller hand in an already-materialised tree -- used by
    the tests, which have no network.
    """
    repo = parse_repo_url(url)
    bare = ensure_bare_clone(repo, root)
    branch = default_branch(bare)
    commit = head_commit(bare, branch)

    tree = checkout
    created = False
    if tree is None:
        from engine.repo.workspace import add_worktree, remove_worktree
        tree = Path(root).resolve() / "_detect" / repo.replace("/", "__")
        add_worktree(bare, commit, tree)
        created = True
    try:
        suite = detect_suite(Path(tree))
    finally:
        if created:
            from engine.repo.workspace import remove_worktree
            remove_worktree(bare, Path(tree))

    if suite is None:
        raise WorkspaceError(
            f"{repo}: no test suite I recognise (looked for pytest, npm test, "
            "go test, cargo test). Refusing to run -- a repo whose tests I "
            "cannot run is a repo whose fix I cannot check."
        )

    owner, name = repo.split("/")
    return RepoTask(
        task_id=f"{owner}__{name}-{commit[:7]}",
        issue=issue,
        repo=repo,
        base_commit=commit,
        default_branch=branch,
        suite=suite,
    )
