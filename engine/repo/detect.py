"""Work out how a cloned repo runs its own tests (repo front door).

Build week: 6.

A SWE-bench task arrives with its environment solved for it: an image, an
install recipe, a test command. An arbitrary GitHub repo arrives with none of
that, so something has to guess -- and a wrong guess is worse than no guess,
because it produces a green suite that never ran the code.

So detection is **conservative and explicit**: a repo either matches a shape we
know, or it is refused. `detect_suite` returns None rather than reaching for a
plausible default, and the front door turns that None into "I cannot work on
this repo", never into a run that reports success.

`per_test` is the field that matters downstream. Only pytest gives us a list of
which tests failed, and without that list there is no way to tell a regression
from a pre-existing failure, and no way to verify a witness test. Suites
without it can still be run -- they just can never return "fixed", only
"unverified" (see engine/eval/repo_grader.py).

Nothing here executes repo code. It reads file names.

Emits: nothing.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path


@dataclass(frozen=True)
class Suite:
    """How to install and test one repo, and what its output can tell us."""

    kind: str                       # pytest | npm | go | cargo
    image: str                      # the Docker image it runs in
    setup: list[list[str]]          # install steps, in order, best effort
    command: list[str]              # the test command itself
    per_test: bool                  # can we name which tests failed?
    why: str = ""                   # what on disk gave it away
    marker_files: list[str] = field(default_factory=list)


def _has(tree: Path, *names: str) -> str | None:
    for n in names:
        if (tree / n).exists():
            return n
    return None


def _looks_like_python_tests(tree: Path) -> bool:
    if (tree / "tests").is_dir() or (tree / "test").is_dir():
        return True
    return any(tree.glob("test_*.py")) or any(tree.glob("*_test.py"))


def detect_suite(tree: Path) -> Suite | None:
    """The repo's own test setup, or None if we do not recognise it.

    Order matters: a Python project with a package.json for its docs site is
    still a Python project, so the language whose *tests* we can read per-test
    is checked first.
    """
    tree = Path(tree)

    py_marker = _has(tree, "pyproject.toml", "setup.py", "setup.cfg", "requirements.txt")
    if py_marker and _looks_like_python_tests(tree):
        setup = [["python", "-m", "pip", "install", "--quiet", "--upgrade", "pip"]]
        # Editable install first because it is what makes `import thepackage`
        # work; requirements as a fallback for repos that are not packages.
        if _has(tree, "pyproject.toml", "setup.py", "setup.cfg"):
            setup.append(["python", "-m", "pip", "install", "--quiet", "-e", "."])
        if (tree / "requirements.txt").is_file():
            setup.append(["python", "-m", "pip", "install", "--quiet",
                          "-r", "requirements.txt"])
        setup.append(["python", "-m", "pip", "install", "--quiet", "pytest"])
        return Suite(
            kind="pytest",
            image="python:3.11-slim",
            setup=setup,
            command=["python", "-m", "pytest", "-q", "--no-header", "-p", "no:cacheprovider"],
            per_test=True,
            why=f"{py_marker} plus a tests directory or test_*.py files",
            marker_files=[py_marker],
        )

    pkg = tree / "package.json"
    if pkg.is_file():
        import json
        try:
            scripts = (json.loads(pkg.read_text()) or {}).get("scripts") or {}
        except (ValueError, OSError):
            scripts = {}
        if "test" in scripts:
            return Suite(
                kind="npm",
                image="node:20-slim",
                setup=[["npm", "install", "--no-audit", "--no-fund"]],
                command=["npm", "test", "--silent"],
                per_test=False,
                why="package.json with a test script",
                marker_files=["package.json"],
            )

    if (tree / "go.mod").is_file():
        return Suite(kind="go", image="golang:1.22", setup=[],
                     command=["go", "test", "./..."], per_test=False,
                     why="go.mod", marker_files=["go.mod"])

    if (tree / "Cargo.toml").is_file():
        return Suite(kind="cargo", image="rust:1-slim", setup=[],
                     command=["cargo", "test", "--quiet"], per_test=False,
                     why="Cargo.toml", marker_files=["Cargo.toml"])

    return None
