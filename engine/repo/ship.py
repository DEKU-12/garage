"""Put a finished repair on a branch, and optionally open a pull request.

Build week: 6.

The last mile of the blueprint: wake up to pull requests. Three rules here are
not negotiable.

  * **Never touch the default branch.** Every write goes to a fresh
    `garage/...` branch, and the branch name is checked against the repo's
    actual trunk rather than a hardcoded "main".
  * **Nothing leaves the machine unless asked.** Committing is local and
    always safe. Pushing and opening a PR are separate, explicit, opt-in steps
    -- an agent that pushes to someone's repo because it felt finished is a
    much worse failure than one that stops.
  * **An unverified fix never becomes a pull request titled like a fix.** The
    caller passes the verdict through; `unverified` is stated in the title and
    the body, every time.

Emits: nothing -- the CLI emits around it.
"""

from __future__ import annotations

import subprocess
from dataclasses import dataclass
from pathlib import Path

from engine.errors import WorkspaceError
from engine.repo.workspace import _git

PROTECTED = {"main", "master", "trunk", "develop", "HEAD"}


@dataclass(frozen=True)
class ShipResult:
    branch: str
    commit: str
    pushed: bool
    pr_url: str = ""


def branch_name(task_id: str, verdict: str) -> str:
    """`garage/fix-...` or `garage/unverified-...`, so the branch itself says
    what it is before anyone opens it."""
    kind = "fix" if verdict == "pass" else "unverified"
    safe = "".join(c if c.isalnum() or c in "-_" else "-" for c in task_id).strip("-")
    return f"garage/{kind}-{safe}"[:100]


def branch_exists(tree: Path, name: str) -> bool:
    return _git(["rev-parse", "--verify", "--quiet", f"refs/heads/{name}"],
                cwd=Path(tree)).returncode == 0


def unique_branch(tree: Path, name: str) -> str:
    """A free branch name, never an existing one.

    The name is derived from repo + commit, so a second run against the same
    commit produces the same name and `checkout -b` fails. Reusing the branch
    would be worse than failing: it would quietly bury the earlier attempt,
    which somebody may not have merged yet. So each run gets its own.
    """
    if not branch_exists(tree, name):
        return name
    for n in range(2, 100):
        candidate = f"{name}-{n}"
        if not branch_exists(tree, candidate):
            return candidate
    raise WorkspaceError(f"{name}: 99 branches already exist for this commit")


def assert_not_protected(branch: str, default: str) -> None:
    if branch in PROTECTED or branch == default:
        raise WorkspaceError(
            f"refusing to write to {branch!r}: the garage never commits to the "
            "branch people work on"
        )


def commit_patch(tree: Path, branch: str, default: str, message: str,
                 author: str = "The Garage <garage@localhost>") -> ShipResult:
    """Branch off the checked-out commit and commit whatever is in the tree.

    The patch is expected to be applied already -- this is the same tree the
    tester graded, so what ships is exactly what passed.
    """
    assert_not_protected(branch, default)
    tree = Path(tree)

    made = _git(["checkout", "-b", branch], cwd=tree)
    if made.returncode != 0:
        # git's own words: "already exists" is a very different problem from a
        # bad name or a detached HEAD, and the caller could not tell them apart.
        raise WorkspaceError(
            f"could not create branch {branch}: {made.stderr.strip()[-200:]}")
    if _git(["add", "-A"], cwd=tree).returncode != 0:
        raise WorkspaceError("git add failed")

    staged = _git(["diff", "--cached", "--name-only"], cwd=tree)
    if not staged.stdout.strip():
        # A commit with no changes would produce a PR that claims a fix and
        # contains nothing -- exactly the shape of bug #11 in the journal.
        raise WorkspaceError("nothing staged: refusing to commit an empty change")

    proc = _git(["-c", f"user.name={author.split(' <')[0]}",
                 "-c", f"user.email={author.split('<')[1].rstrip('>')}",
                 "commit", "-m", message], cwd=tree)
    if proc.returncode != 0:
        raise WorkspaceError(f"commit failed: {proc.stderr[-400:]}")
    sha = _git(["rev-parse", "HEAD"], cwd=tree).stdout.strip()
    return ShipResult(branch=branch, commit=sha, pushed=False)


def push_branch(tree: Path, branch: str, remote: str = "origin") -> None:
    """Send the branch to the remote. Network. Opt-in only."""
    proc = _git(["push", "--set-upstream", remote, branch], cwd=Path(tree),
                timeout_s=600)
    if proc.returncode != 0:
        raise WorkspaceError(f"push failed: {proc.stderr[-500:]}")


def pr_body(task: object, verdict: str, reason: str, witness: list[str],
            regressions: list[str], attempts: int) -> str:
    """The PR body says exactly what was and was not proved."""
    lines = [
        "Opened by **The Garage**, an autonomous repair agent.",
        "",
        f"- outcome: **{verdict}**" + (f" ({reason})" if reason else ""),
        f"- attempts: {attempts}",
    ]
    if witness:
        lines.append(f"- witness test(s), failing before and passing after: "
                     f"{', '.join(witness)}")
    else:
        lines.append("- **no witness test.** This change has not been shown to "
                     "fix anything; it only leaves the existing suite green.")
    if regressions:
        lines.append(f"- regressions: {', '.join(regressions)}")
    lines += ["", "### How to check this yourself", "",
              "1. Run the suite on the base commit.",
              "2. Apply this branch and run it again.",
              "3. Confirm the witness test fails on (1) and passes on (2).",
              "",
              "If there is no witness test above, treat this as a suggestion, "
              "not a fix."]
    return "\n".join(lines)


def open_pr(tree: Path, branch: str, base: str, title: str, body: str,
            runner=None) -> str:
    """Open the PR with the gh CLI. Network. Opt-in only."""
    run = runner or (lambda argv, cwd: subprocess.run(
        argv, cwd=cwd, capture_output=True, text=True, timeout=300, check=False))
    proc = run(["gh", "pr", "create", "--base", base, "--head", branch,
                "--title", title, "--body", body], Path(tree))
    if getattr(proc, "returncode", 1) != 0:
        raise WorkspaceError(f"gh pr create failed: {getattr(proc, 'stderr', '')[-500:]}")
    return (proc.stdout or "").strip().splitlines()[-1] if proc.stdout else ""
