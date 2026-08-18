"""Diff extraction, validation, and apply -- the highest-risk component (R3).

Build week: 1. Defense in depth (TAD §3.4):

  1. prompt contract  -- builder.md shows one worked `diff --git` example and
                         forbids prose (lives with the prompt, not here)
  2. extraction       -- pull the diff out of whatever the model wrapped it in
  3. validation       -- paths exist, hunks parse; reject early with a reason
                         specific enough to hand back to the builder
  4. apply            -- git apply in the attempt's worktree, stderr captured
  5. on failure       -- git's own error goes back to the builder VERBATIM

Apply failures count toward the correctness cap (that is what stops an infinite
malformed-diff loop) but are reported as their own failure type (FR-4):
"the model can't format a diff" and "the model can't fix the bug" are different
findings, and collapsing them would hide the more fixable one.

Emits: patch_produced, patch_apply_error.
"""

from __future__ import annotations

import re
import subprocess
from dataclasses import dataclass
from pathlib import Path

from engine.errors import PatchError

APPLY_TIMEOUT_S = 60

_FENCE = re.compile(r"```[\w+-]*\n(.*?)(?:```|\Z)", re.DOTALL)
_DIFF_START = re.compile(r"^diff --git ", re.MULTILINE)
_MINUS_START = re.compile(r"^--- ", re.MULTILINE)
# MULTILINE matters: without it ^ only anchors at position 0 and every
# hunk header after the file headers is invisible.
_HUNK = re.compile(r"^@@ -\d+(?:,\d+)? \+\d+(?:,\d+)? @@", re.MULTILINE)
_GIT_HEADER = re.compile(r"^diff --git a/(?P<a>.+?) b/(?P<b>.+?)\s*$", re.MULTILINE)
_PLUS_FILE = re.compile(r"^\+\+\+ (?:b/)?(?P<path>.+?)\s*$", re.MULTILINE)
_NEW_FILE = re.compile(r"^new file mode ", re.MULTILINE)


@dataclass(frozen=True)
class ApplyResult:
    """What happened when git tried to apply a diff."""

    applied: bool
    mode: str  # "3way" | "plain" | "" when nothing applied
    stderr: str  # git's own words, for feedback to the builder
    files: list[str]


def extract_diff(text: str) -> str:
    """Pull a unified diff out of a model response.

    Models wrap diffs in fences, prefix them with explanation, and sometimes
    emit several blocks. Take the last fenced block that actually looks like a
    diff, then drop anything before the first `diff --git` / `---` line.

    Raises PatchError with a reason the builder can act on.
    """
    if not text or not text.strip():
        raise PatchError("model returned an empty response, no diff to extract")

    blocks = [b for b in _FENCE.findall(text)]
    candidates = [b for b in blocks if _DIFF_START.search(b) or _MINUS_START.search(b)]
    body = candidates[-1] if candidates else (blocks[-1] if blocks else text)

    start = _DIFF_START.search(body) or _MINUS_START.search(body)
    if start is None:
        raise PatchError(
            "no unified diff found in the response: expected a line starting "
            "with 'diff --git' or '---'"
        )
    diff = body[start.start():].rstrip()
    return diff + "\n"


def diff_paths(diff: str) -> list[str]:
    """Repo-relative paths the diff touches, in order, de-duplicated."""
    paths = [m.group("b") for m in _GIT_HEADER.finditer(diff)]
    if not paths:
        paths = [
            m.group("path")
            for m in _PLUS_FILE.finditer(diff)
            if m.group("path") != "/dev/null"
        ]
    seen: dict[str, None] = {}
    for p in paths:
        seen.setdefault(p, None)
    return list(seen)


def validate_diff(diff: str, tree: Path) -> list[str]:
    """Structural checks before git ever sees the diff.

    Cheap, and the failure messages are far more actionable than git's. Returns
    the touched paths. Raises PatchError naming exactly what is wrong.
    """
    # Specific before general: a mangled "@@ -x,y +z,w @@" is a malformed
    # header, not a missing one, and the builder can only fix what we name.
    for line in diff.splitlines():
        if line.startswith("@@") and not _HUNK.match(line):
            raise PatchError(f"malformed hunk header: {line[:80]!r}")

    if not _HUNK.search(diff) and not _NEW_FILE.search(diff):
        raise PatchError("diff contains no hunk header (expected a line like '@@ -1,4 +1,6 @@')")

    paths = diff_paths(diff)
    if not paths:
        raise PatchError("diff names no files (missing 'diff --git' / '+++' headers)")

    creating = bool(_NEW_FILE.search(diff))
    missing = [p for p in paths if not (Path(tree) / p).exists()]
    if missing and not creating:
        raise PatchError(
            "diff targets files that do not exist in the checkout: "
            + ", ".join(missing[:5])
        )
    return paths


def _run_apply(diff: str, tree: Path, extra: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", "apply", *extra, "-"],
        cwd=tree,
        input=diff,
        capture_output=True,
        text=True,
        timeout=APPLY_TIMEOUT_S,
        check=False,
    )


def apply_patch(diff: str, tree: Path) -> ApplyResult:
    """Apply a diff to a worktree, trying --3way first and plain apply second.

    --3way is the better mode: it can reconcile a diff whose context has
    drifted. It needs the pre-image blobs named in the diff's `index` lines,
    though, and model-written diffs routinely omit those -- so plain apply is
    the fallback, not the other way round.

    Never raises on a bad patch: a rejected diff is data (rules.md §3.1). The
    stderr comes back verbatim so the builder gets git's exact complaint.
    """
    files = diff_paths(diff)

    for mode, extra in (("3way", ["--3way"]), ("plain", [])):
        proc = _run_apply(diff, tree, extra)
        if proc.returncode == 0:
            return ApplyResult(applied=True, mode=mode, stderr="", files=files)
        last_stderr = proc.stderr.strip()

    return ApplyResult(applied=False, mode="", stderr=last_stderr, files=files)


def tree_diff(tree: Path) -> str:
    """The worktree's full diff against its base commit -- the patch we submit.

    Always `git diff HEAD`, never plain `git diff`. `git apply --3way` implies
    `--index`, so a successful three-way apply leaves the change STAGED and
    plain `git diff` reports an empty tree. Submitting that empty string scores
    the task `empty_patch` in the harness: a silent zero that is indis-
    tinguishable from the model having failed. `git diff HEAD` covers staged
    and unstaged changes alike, so it reads the same under either apply mode.
    """
    proc = subprocess.run(
        ["git", "diff", "HEAD"],
        cwd=tree,
        capture_output=True,
        text=True,
        timeout=APPLY_TIMEOUT_S,
        check=False,
    )
    if proc.returncode != 0:
        raise PatchError(f"could not read diff from {tree}: {proc.stderr[-300:]}")
    return proc.stdout
