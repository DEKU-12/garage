"""Fresh git worktree per attempt, off a shared bare clone (ADR-4).

Build week: 1.

One bare clone per repo is cached in `workspaces/_bare/`; every attempt gets
its own worktree off it. Worktrees are near-free (they share the object store),
which is what makes "clean checkout per retry" affordable -- and a clean
checkout is what guarantees attempt N+1 never inherits a half-applied diff from
attempt N.

Nothing here executes repo code. Checkouts are inert files; tests only ever run
inside the SWE-bench Docker harness (NFR-3).

Emits: nothing.
"""

from __future__ import annotations

import shutil
import subprocess
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from engine.errors import WorkspaceError

BARE_DIR = "_bare"
CLONE_TIMEOUT_S = 1800  # a cold clone of django/sympy is not fast
GIT_TIMEOUT_S = 300


def _git(
    args: list[str], cwd: Path | None = None, timeout_s: int = GIT_TIMEOUT_S
) -> subprocess.CompletedProcess[str]:
    """Run one git command. Never raises on a non-zero exit -- callers decide."""
    try:
        return subprocess.run(
            ["git", *args],
            cwd=cwd,
            capture_output=True,
            text=True,
            timeout=timeout_s,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        raise WorkspaceError(f"git {' '.join(args[:2])} timed out after {timeout_s}s") from exc


def bare_path(repo: str, root: Path) -> Path:
    """workspaces/_bare/django__django.git for repo 'django/django'.

    Absolute, always. Every git call here runs with cwd set to the bare repo,
    so a relative path would resolve against *that* -- which silently plants
    worktrees inside the bare clone instead of where the caller asked.
    """
    return Path(root).resolve() / BARE_DIR / f"{repo.replace('/', '__')}.git"


def ensure_bare_clone(repo: str, root: Path) -> Path:
    """Clone `repo` bare once and reuse it forever after."""
    dest = bare_path(repo, root)
    if (dest / "HEAD").is_file():
        return dest

    dest.parent.mkdir(parents=True, exist_ok=True)
    url = f"https://github.com/{repo}.git"
    proc = _git(["clone", "--bare", url, str(dest)], timeout_s=CLONE_TIMEOUT_S)
    if proc.returncode != 0:
        shutil.rmtree(dest, ignore_errors=True)  # never leave a half clone behind
        raise WorkspaceError(f"clone of {url} failed: {proc.stderr[-800:]}")
    return dest


def ensure_commit(bare: Path, commit: str) -> None:
    """Make sure `commit` is present locally, fetching it if the clone is stale."""
    if _git(["cat-file", "-e", f"{commit}^{{commit}}"], cwd=bare).returncode == 0:
        return
    proc = _git(["fetch", "origin", commit], cwd=bare, timeout_s=CLONE_TIMEOUT_S)
    if proc.returncode != 0:
        raise WorkspaceError(f"{bare.name}: cannot fetch {commit}: {proc.stderr[-500:]}")


def add_worktree(bare: Path, commit: str, dest: Path) -> Path:
    """Check `commit` out into its own worktree at `dest`."""
    dest = Path(dest).resolve()
    if dest.exists():
        remove_worktree(bare, dest)
    dest.parent.mkdir(parents=True, exist_ok=True)
    proc = _git(["worktree", "add", "--detach", str(dest), commit], cwd=bare)
    if proc.returncode != 0:
        raise WorkspaceError(
            f"{bare.name}: worktree add {commit[:12]} failed: {proc.stderr[-500:]}"
        )
    return dest


def remove_worktree(bare: Path, dest: Path) -> None:
    """Drop a worktree and its bookkeeping. Best effort -- never blocks a run."""
    dest = Path(dest).resolve()
    _git(["worktree", "remove", "--force", str(dest)], cwd=bare)
    if dest.exists():
        shutil.rmtree(dest, ignore_errors=True)
    _git(["worktree", "prune"], cwd=bare)


@contextmanager
def attempt_worktree(
    repo: str, commit: str, task_id: str, attempt: int, root: Path, keep: bool = False
) -> Iterator[Path]:
    """A clean checkout for one builder attempt, cleaned up on the way out.

    `keep=True` leaves the tree on disk for debugging a failed attempt.
    """
    bare = ensure_bare_clone(repo, root)
    ensure_commit(bare, commit)
    dest = Path(root).resolve() / task_id / str(attempt)
    add_worktree(bare, commit, dest)
    try:
        yield dest
    finally:
        if not keep:
            remove_worktree(bare, dest)
