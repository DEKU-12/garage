"""Break working code on purpose, so repairs can be measured honestly.

Build week: 6.

SWE-bench tasks are public and years old. Measured on this project's own E1
run, **10 of 13 solved tasks reproduced the human fix verbatim** -- one of them
17 added lines, identical. Whatever that measures, it is not the ability to
repair code it has not seen.

A mutant has no such problem. The bug did not exist until this module wrote it,
so there is no published fix to recall: the model has to read the failing test,
read the code, and work it out. That is the thing worth measuring.

It also gives back something repo mode cannot have -- a real `fail_to_pass`
list. We know which tests the mutation broke because we ran them before and
after, so grading is a comparison, not an inference. And because the original
line is kept, "did it find THE fix" is answerable, not just "did the tests go
green".

HOW A MUTATION IS MADE. The AST chooses what to break; the edit is applied to
the source text inside that node's own span. Going through `ast.unparse` would
be simpler and would also reformat the entire file and drop every comment,
burying a one-character bug inside a thousand-line diff. Restricting the edit
to the node's span is also what stops a `>` inside a docstring being mutated.

Every mutant is re-parsed before being offered. A mutation that will not parse
is not a bug, it is a broken file, and scoring a model against it would be
measuring nothing.

WHAT THIS DOES NOT MEASURE: a flipped comparison with a failing test attached
is an easier problem than a vague issue report. This is a narrower distribution
than real bugs -- but an honestly measured one, which the benchmark is not.

Emits: nothing.
"""

from __future__ import annotations

import ast
import random
import re
from dataclasses import dataclass
from pathlib import Path

# (pattern, replacement, name). Order matters: >= must be tried before >.
SWAPS: list[tuple[str, str, str]] = [
    (r">=", "<", "ge_to_lt"),
    (r"<=", ">", "le_to_gt"),
    (r"==", "!=", "eq_to_ne"),
    (r"!=", "==", "ne_to_eq"),
    (r"\bis not\b", "is", "isnot_to_is"),
    (r"\bnot in\b", "in", "notin_to_in"),
    (r">", ">=", "gt_to_ge"),
    (r"<", "<=", "lt_to_le"),
    (r"\band\b", "or", "and_to_or"),
    (r"\bor\b", "and", "or_to_and"),
    (r"\bnot\s+", "", "drop_not"),
    (r"\bTrue\b", "False", "true_to_false"),
    (r"\bFalse\b", "True", "false_to_true"),
]

# Nodes worth breaking. A mutation here changes behaviour rather than style.
TARGETS = (ast.Compare, ast.BoolOp, ast.UnaryOp)

SKIP_DIRS = ("test", "tests", "docs", "examples", "scripts", "migrations")

# Not the repo's code. Mutating a vendored dependency asks the model to repair
# a library it did not write, in a checkout the maintainers never touch --
# and the first run of this did exactly that, injecting bugs into pytest
# itself inside a .venv. Every candidate came from site-packages.
NOT_THE_REPO = (
    ".venv", "venv", "env", ".env", "site-packages", "dist-packages",
    "node_modules", ".git", ".tox", ".nox", "build", "dist", ".eggs",
    "__pycache__", ".mypy_cache", ".pytest_cache", "vendor", "third_party",
)


@dataclass(frozen=True)
class Mutant:
    """One deliberate one-line wrongness, and the truth it replaced."""

    mid: str          # stable identifier, safe for a filename
    path: str         # repo-relative
    line: int
    operator: str
    before: str       # the original line -- the ground truth for "the" fix
    after: str        # the line as broken
    source: str       # the whole mutated file

    @property
    def summary(self) -> str:
        return f"{self.path}:{self.line} [{self.operator}]"


def is_source_file(rel: str) -> bool:
    """Source only, and the repo's OWN source.

    Tests are excluded because a 'fix' could otherwise be deleting the test.
    Vendored code is excluded because it is not the repo's to repair.
    """
    parts = Path(rel).parts
    if any(p in NOT_THE_REPO for p in parts):
        return False
    if any(p.endswith(".egg-info") for p in parts):
        return False
    if any(p in SKIP_DIRS for p in parts):
        return False
    name = Path(rel).name
    return (name.endswith(".py") and not name.startswith("test_")
            and not name.endswith("_test.py") and name != "conftest.py"
            and not name.startswith("__"))


def candidates(tree: Path, rel: str) -> list[tuple[int, int, int]]:
    """(line, col, end_col) spans worth mutating, from the AST."""
    try:
        src = (tree / rel).read_text(encoding="utf-8")
        parsed = ast.parse(src)
    except (OSError, SyntaxError, UnicodeDecodeError):
        return []
    out = []
    for node in ast.walk(parsed):
        if not isinstance(node, TARGETS):
            continue
        if isinstance(node, ast.UnaryOp) and not isinstance(node.op, ast.Not):
            continue
        # single-line nodes only: a span across lines makes the text edit
        # ambiguous, and there are plenty of single-line ones
        if getattr(node, "end_lineno", None) != node.lineno:
            continue
        out.append((node.lineno, node.col_offset, node.end_col_offset))
    return out


def make(tree: Path, rel: str, span: tuple[int, int, int]) -> Mutant | None:
    """Apply the first swap that fits inside this node's span."""
    line_no, col, end_col = span
    src = (tree / rel).read_text(encoding="utf-8")
    lines = src.splitlines(keepends=True)
    if line_no > len(lines):
        return None
    line = lines[line_no - 1]

    # `if __name__ == "__main__"` flips to running a script on import, which
    # breaks the suite at collection time in a way that has nothing to do with
    # the logic under test. It would be trivially "fixed" and would pollute the
    # measurement with a task that is not a bug.
    if "__name__" in line:
        return None

    head, node_text, tail = line[:col], line[col:end_col], line[end_col:]

    for pattern, repl, name in SWAPS:
        mutated_node, n = re.subn(pattern, repl, node_text, count=1)
        if not n or mutated_node == node_text:
            continue
        new_line = head + mutated_node + tail
        candidate = "".join(lines[:line_no - 1] + [new_line] + lines[line_no:])
        try:
            ast.parse(candidate)          # a broken file is not a bug
        except SyntaxError:
            continue
        if candidate == src:
            continue
        return Mutant(
            mid=f"{Path(rel).stem}-L{line_no}-{name}",
            path=rel, line=line_no, operator=name,
            before=line.rstrip("\n"), after=new_line.rstrip("\n"),
            source=candidate,
        )
    return None


def generate(tree: Path, limit: int = 80, seed: int = 0) -> list[Mutant]:
    """Up to `limit` mutants, spread across files and deterministic per seed.

    Spread matters: fifty mutations of one hot function would measure one
    function. Files are sampled round-robin so the set covers the codebase.
    """
    tree = Path(tree)
    files = sorted(
        str(p.relative_to(tree)) for p in tree.rglob("*.py")
        if is_source_file(str(p.relative_to(tree)))
    )
    rng = random.Random(seed)
    pools = {}
    for rel in files:
        spans = candidates(tree, rel)
        rng.shuffle(spans)
        if spans:
            pools[rel] = spans

    out: list[Mutant] = []
    seen: set[str] = set()
    while pools and len(out) < limit:
        for rel in list(pools):
            if len(out) >= limit:
                break
            span = pools[rel].pop()
            if not pools[rel]:
                del pools[rel]
            m = make(tree, rel, span)
            if m and m.mid not in seen:
                seen.add(m.mid)
                out.append(m)
    return out
