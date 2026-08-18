"""ripgrep wrapper: `rg --json` via subprocess (FR-14).

Build week: 2. Not a Python re-implementation (rules.md §2.1) -- rg walks a
500MB checkout in milliseconds and already knows to skip .git and binaries.

Timeout 10s, per rules.md §3.2: timeouts on everything external.

Emits: nothing.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

SEARCH_TIMEOUT_S = 10
DEFAULT_GLOBS = ("*.py",)
# Directories that are never the site of a SWE-bench fix but that dominate a
# naive search: vendored code, generated migrations, the project's own tests.
SKIP_GLOBS = (
    "!**/tests/**", "!**/test/**", "!**/*_test.py", "!**/test_*.py",
    "!**/migrations/**", "!**/node_modules/**", "!**/.tox/**",
    "!**/docs/**", "!**/build/**", "!**/dist/**",
)


@dataclass(frozen=True)
class Hit:
    """One matching line."""

    path: str  # repo-relative
    line: int
    text: str


def available() -> bool:
    """Is ripgrep on PATH? Callers degrade rather than crash."""
    return shutil.which("rg") is not None


def search(
    pattern: str,
    tree: Path,
    *,
    max_hits: int = 40,
    globs: tuple[str, ...] = DEFAULT_GLOBS,
    include_tests: bool = False,
    fixed: bool = True,
) -> list[Hit]:
    """Search `tree` for `pattern`, returning at most `max_hits` lines.

    `fixed=True` treats the pattern literally -- issue text is full of regex
    metacharacters ("__init__", "r'^[\\w.@+-]+$'") that would otherwise either
    error or match nonsense.

    A failed search returns [] rather than raising: retrieval is best-effort,
    and a task must never crash because a keyword was odd.
    """
    if not available():
        return []

    cmd = ["rg", "--json", "--line-number", "--no-heading"]
    cmd += ["--fixed-strings"] if fixed else []
    cmd += ["--max-count", str(max_hits)]
    for g in globs:
        cmd += ["--glob", g]
    if not include_tests:
        for g in SKIP_GLOBS:
            cmd += ["--glob", g]
    cmd += ["--", pattern, "."]

    try:
        proc = subprocess.run(
            cmd, cwd=tree, capture_output=True, text=True,
            timeout=SEARCH_TIMEOUT_S, check=False,
        )
    except subprocess.TimeoutExpired:
        return []

    hits: list[Hit] = []
    for raw in proc.stdout.splitlines():
        try:
            event = json.loads(raw)
        except json.JSONDecodeError:
            continue  # rg emits one JSON object per line; skip anything odd
        if event.get("type") != "match":
            continue
        data = event["data"]
        hits.append(
            Hit(
                path=data["path"]["text"].removeprefix("./"),
                line=data["line_number"],
                text=data["lines"]["text"].rstrip("\n")[:300],
            )
        )
        if len(hits) >= max_hits:
            break
    return hits
