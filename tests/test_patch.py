"""Patch extraction/validation/apply -- the R3 component (rules.md §4.3).

Offline by construction: a throwaway git repo in tmp_path, no network, no
Docker, no model. Runs in CI.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from engine.errors import PatchError
from engine.repo.patch import (
    apply_patch,
    diff_paths,
    extract_diff,
    tree_diff,
    validate_diff,
)

GOOD_DIFF = """diff --git a/hello.py b/hello.py
--- a/hello.py
+++ b/hello.py
@@ -1,2 +1,2 @@
 def greet():
-    return 'hi'
+    return 'hello'
"""


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    """A one-file git repo at a known commit."""
    (tmp_path / "hello.py").write_text("def greet():\n    return 'hi'\n")
    run = lambda *a: subprocess.run(["git", *a], cwd=tmp_path, capture_output=True, check=True)
    run("init", "-q", "-b", "main")
    run("-c", "user.name=t", "-c", "user.email=t@t", "add", "hello.py")
    run("-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "-m", "init")
    return tmp_path


# --- extraction -----------------------------------------------------------

def test_extracts_from_fenced_block() -> None:
    assert extract_diff(f"Here you go:\n```diff\n{GOOD_DIFF}```\nDone.") == GOOD_DIFF


def test_prefers_the_last_block_that_is_actually_a_diff() -> None:
    """Models love to show a code sample before the real patch."""
    messy = f"```python\nreturn 'hi'  # the bug\n```\nFix:\n```diff\n{GOOD_DIFF}```"
    got = extract_diff(messy)
    assert got == GOOD_DIFF
    assert "# the bug" not in got


def test_accepts_an_unfenced_diff() -> None:
    assert extract_diff(f"I'll fix it.\n\n{GOOD_DIFF}") == GOOD_DIFF


def test_prose_only_is_rejected_with_a_usable_reason() -> None:
    with pytest.raises(PatchError, match="no unified diff"):
        extract_diff("Just change 'hi' to 'hello' in the greet function.")


def test_empty_response_is_rejected() -> None:
    with pytest.raises(PatchError, match="empty response"):
        extract_diff("   \n  ")


def test_diff_paths_dedupes_in_order() -> None:
    assert diff_paths(GOOD_DIFF) == ["hello.py"]


# --- validation -----------------------------------------------------------

def test_validate_accepts_a_real_diff(repo: Path) -> None:
    assert validate_diff(GOOD_DIFF, repo) == ["hello.py"]


def test_validate_rejects_a_path_not_in_the_checkout(repo: Path) -> None:
    bad = GOOD_DIFF.replace("hello.py", "ghost.py")
    with pytest.raises(PatchError, match="do not exist"):
        validate_diff(bad, repo)


def test_validate_rejects_a_diff_with_no_hunks(repo: Path) -> None:
    headers = "diff --git a/hello.py b/hello.py\n--- a/hello.py\n+++ b/hello.py\n"
    with pytest.raises(PatchError, match="no hunk header"):
        validate_diff(headers, repo)


def test_validate_rejects_a_malformed_hunk_header(repo: Path) -> None:
    bad = GOOD_DIFF.replace("@@ -1,2 +1,2 @@", "@@ -x,y +z,w @@")
    with pytest.raises(PatchError, match="malformed hunk header"):
        validate_diff(bad, repo)


def test_validate_allows_a_new_file(repo: Path) -> None:
    creating = (
        "diff --git a/brand_new.py b/brand_new.py\n"
        "new file mode 100644\n--- /dev/null\n+++ b/brand_new.py\n"
        "@@ -0,0 +1,1 @@\n+x = 1\n"
    )
    assert validate_diff(creating, repo) == ["brand_new.py"]


# --- apply ----------------------------------------------------------------

def test_apply_succeeds_and_changes_the_file(repo: Path) -> None:
    result = apply_patch(GOOD_DIFF, repo)
    assert result.applied
    assert result.files == ["hello.py"]
    assert "hello" in (repo / "hello.py").read_text()


def test_apply_failure_returns_gits_own_words_and_never_raises(repo: Path) -> None:
    """A rejected diff is data, not an exception (rules.md §3.1)."""
    drifted = GOOD_DIFF.replace("def greet():", "def totally_different():")
    result = apply_patch(drifted, repo)
    assert not result.applied
    assert "does not apply" in result.stderr or "patch failed" in result.stderr
    assert (repo / "hello.py").read_text() == "def greet():\n    return 'hi'\n"


def test_tree_diff_sees_staged_changes(repo: Path) -> None:
    """--3way implies --index; plain `git diff` would report an empty tree."""
    assert apply_patch(GOOD_DIFF, repo).applied
    out = tree_diff(repo)
    assert "hello.py" in out
    assert "+    return 'hello'" in out


def test_tree_diff_is_empty_on_an_untouched_tree(repo: Path) -> None:
    assert tree_diff(repo) == ""


# --- hunk header repair (R3) ----------------------------------------------

BARE_HEADER_DIFF = """diff --git a/hello.py b/hello.py
--- a/hello.py
+++ b/hello.py
@@
 def greet():
-    return 'hi'
+    return 'hello'
"""


def test_bare_header_is_unusable_before_repair(repo: Path) -> None:
    """Establishes the problem: git rejects a bare @@ outright."""
    assert not apply_patch(BARE_HEADER_DIFF, repo).applied


def test_repair_computes_the_header_and_the_patch_then_applies(repo: Path) -> None:
    from engine.repo.patch import repair_hunk_headers

    fixed, notes = repair_hunk_headers(BARE_HEADER_DIFF, repo)
    assert "@@ -1,2 +1,2 @@" in fixed
    assert notes and "hello.py" in notes[0]
    assert apply_patch(fixed, repo).applied
    assert "hello" in (repo / "hello.py").read_text()


def test_repair_never_changes_which_lines_are_added_or_removed(repo: Path) -> None:
    """Repair fixes arithmetic only -- it must not touch the actual edit."""
    from engine.repo.patch import repair_hunk_headers

    fixed, _ = repair_hunk_headers(BARE_HEADER_DIFF, repo)
    edits = lambda d: [l for l in d.splitlines() if l[:1] in "+-" and not l.startswith(("+++", "---"))]
    assert edits(fixed) == edits(BARE_HEADER_DIFF)


def test_repair_distinguishes_near_identical_hunks(tmp_path: Path) -> None:
    """Two blocks differing only in name: a fresh search maps both onto the first."""
    (tmp_path / "two.py").write_text(
        "class A:\n    regex = 'x'\n    code = 1\n\n"
        "class B:\n    regex = 'x'\n    code = 2\n"
    )
    run = lambda *a: subprocess.run(["git", *a], cwd=tmp_path, capture_output=True, check=True)
    run("init", "-q", "-b", "main")
    run("-c", "user.name=t", "-c", "user.email=t@t", "add", "two.py")
    run("-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "-m", "i")

    from engine.repo.patch import repair_hunk_headers

    diff = """diff --git a/two.py b/two.py
--- a/two.py
+++ b/two.py
@@
 class A:
-    regex = 'x'
+    regex = 'y'
     code = 1
@@
 class B:
-    regex = 'x'
+    regex = 'y'
     code = 2
"""
    fixed, _ = repair_hunk_headers(diff, tmp_path)
    headers = [l for l in fixed.splitlines() if l.startswith("@@")]
    assert headers == ["@@ -1,3 +1,3 @@", "@@ -5,3 +5,3 @@"], headers
    assert apply_patch(fixed, tmp_path).applied


def test_repair_rejects_invented_context_with_actionable_feedback(repo: Path) -> None:
    """A model that never saw the file writes context that isn't there."""
    from engine.repo.patch import repair_hunk_headers

    invented = BARE_HEADER_DIFF.replace("def greet():", "def some_other_function():")
    with pytest.raises(PatchError, match="do not appear in the file"):
        repair_hunk_headers(invented, repo)
