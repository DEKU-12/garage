"""Diff extraction, validation, and apply -- the highest-risk component (R3).

Build week: 1. Defense in depth (TAD §3.4):
  1. prompt contract (one worked diff --git example, prose forbidden)
  2. extraction: last fenced block, strip anything before diff --git / ---
  3. validation: paths exist in the checkout, hunk headers parse
  4. apply: git apply --3way in the attempt worktree, capture stderr
  5. on failure: emit patch_apply_error, feed git's stderr back VERBATIM

Apply failures count toward the correctness cap (prevents infinite
malformed-diff loops) but are reported as their own failure type (FR-4) --
"can't format diffs" and "can't fix bugs" are different findings.

Emits: patch_produced, patch_apply_error.
"""
