"""AST repo map: file tree + function/class signatures (FR-13).

Build week: 2. stdlib `ast` first (rules.md §2.1); tree-sitter only if
non-Python repos enter scope.

A whole-repo map is the textbook approach and does not survive contact with
django: ~2800 Python files is far past any context budget, and far past the
free tier's 8000 tokens/minute ceiling. So the map here is built for a
*selected* set of files -- search picks the candidates, this describes them.

Emits: nothing.
"""

from __future__ import annotations

import ast
from dataclasses import dataclass
from pathlib import Path

SKIP_PARTS = {
    "tests", "test", "migrations", "node_modules", ".tox", ".git",
    "docs", "build", "dist", "__pycache__", ".venv",
}


@dataclass(frozen=True)
class Symbol:
    """A class or function, with the line span it actually occupies."""

    kind: str  # "class" | "def"
    name: str
    qualname: str
    start: int
    end: int
    doc: str  # first line of the docstring, "" when absent


def _docline(node: ast.AST) -> str:
    doc = ast.get_docstring(node) or ""
    return doc.strip().splitlines()[0][:120] if doc.strip() else ""


def outline(source: str) -> list[Symbol]:
    """Top-level classes/functions plus one level of methods.

    Two levels is the useful depth: it names the method a fix belongs in
    without drowning the pack in nested helpers.
    """
    try:
        module = ast.parse(source)
    except SyntaxError:
        return []  # a file we cannot parse is not worth crashing a task over

    found: list[Symbol] = []
    for node in module.body:
        if isinstance(node, ast.ClassDef):
            found.append(
                Symbol("class", node.name, node.name, node.lineno,
                       getattr(node, "end_lineno", node.lineno), _docline(node))
            )
            for child in node.body:
                if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    found.append(
                        Symbol("def", child.name, f"{node.name}.{child.name}",
                               child.lineno,
                               getattr(child, "end_lineno", child.lineno),
                               _docline(child))
                    )
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            found.append(
                Symbol("def", node.name, node.name, node.lineno,
                       getattr(node, "end_lineno", node.lineno), _docline(node))
            )
    return found


def outline_file(tree: Path, rel_path: str) -> list[Symbol]:
    """Outline one repo-relative file. Unreadable files yield []."""
    target = Path(tree) / rel_path
    try:
        return outline(target.read_text(encoding="utf-8", errors="replace"))
    except OSError:
        return []


def enclosing_symbol(symbols: list[Symbol], line: int) -> Symbol | None:
    """The tightest symbol containing `line` -- the method a hit lives in.

    Narrowest wins: a method inside a class beats the class itself, which is
    the difference between showing the builder 15 lines and 400.
    """
    containing = [s for s in symbols if s.start <= line <= s.end]
    return min(containing, key=lambda s: s.end - s.start) if containing else None


def read_span(tree: Path, rel_path: str, start: int, end: int) -> str:
    """Source lines [start, end], 1-based and inclusive, as they really are.

    The builder's most common failure in week 1 was inventing context lines
    that do not exist. Handing it the real bytes is the fix.
    """
    try:
        lines = (Path(tree) / rel_path).read_text(
            encoding="utf-8", errors="replace"
        ).splitlines()
    except OSError:
        return ""
    lo = max(1, start) - 1
    hi = min(len(lines), end)
    return "\n".join(lines[lo:hi])


def is_interesting(rel_path: str) -> bool:
    """Is this a file a fix would plausibly live in?"""
    parts = Path(rel_path).parts
    if any(p in SKIP_PARTS for p in parts):
        return False
    name = Path(rel_path).name
    return rel_path.endswith(".py") and not (
        name.startswith("test_") or name.endswith("_test.py")
    )
