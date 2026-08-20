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

import re
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


def _sh_safe(req: str) -> str:
    """Quote a requirement for a shell command line -- `pkg>=1.2` needs it."""
    return "'" + req.replace("'", "'\\''") + "'"


def _has(tree: Path, *names: str) -> str | None:
    for n in names:
        if (tree / n).exists():
            return n
    return None


def _looks_like_python_tests(tree: Path) -> bool:
    if (tree / "tests").is_dir() or (tree / "test").is_dir():
        return True
    return any(tree.glob("test_*.py")) or any(tree.glob("*_test.py"))


# A suite that needs a database or a message broker standing up alongside it
# cannot be run by `docker run` over a checkout. Detecting the intent lets the
# refusal say WHY, which is the difference between a bug report and a shrug.
COMPOSE_FILES = ("docker-compose.yml", "docker-compose.yaml",
                 "compose.yml", "compose.yaml")


# Where projects put the dependencies their TESTS need, as opposed to the ones
# the package needs to run. `pip install -e .` installs neither of these:
# PEP 735 dependency-groups are not installed by default, and extras must be
# named explicitly. A container that skips them looks like a repo whose tests
# are broken -- six collection errors on this project, from one missing httpx.
TEST_GROUPS = ("dev", "test", "tests", "testing", "dev-dependencies")


def python_image(tree: Path, default: str = "python:3.11-slim") -> str:
    """The interpreter the repo says it needs.

    A repo pinning `requires-python = "==3.12.*"` cannot be installed by a
    3.11 interpreter: pip refuses outright. Combined with a tolerant
    `|| true`, that silently produced a container with NONE of the project's
    dependencies -- and a suite failing with ModuleNotFoundError, which reads
    as a broken repo rather than a wrong image.

    Only the interpreter is chosen here, never the version constraint's full
    semantics: the first 3.x mentioned wins, and anything unparseable falls
    back to the default rather than guessing.
    """
    pyproject = Path(tree) / "pyproject.toml"
    if not pyproject.is_file():
        return default
    try:
        import tomllib
        spec = ((tomllib.loads(pyproject.read_text(encoding="utf-8"))
                 .get("project") or {}).get("requires-python") or "")
    except (OSError, ValueError, ImportError):
        return default
    m = re.search(r"3\.(\d+)", spec)
    if not m:
        return default
    minor = int(m.group(1))
    return f"python:3.{minor}-slim" if 8 <= minor <= 13 else default


def test_requirements(tree: Path) -> list[str]:
    """Requirement strings a repo declares for its own test suite."""
    pyproject = Path(tree) / "pyproject.toml"
    if not pyproject.is_file():
        return []
    try:
        import tomllib
        data = tomllib.loads(pyproject.read_text(encoding="utf-8"))
    except (OSError, ValueError, ImportError):
        return []

    found: list[str] = []
    groups = data.get("dependency-groups") or {}
    extras = (data.get("project") or {}).get("optional-dependencies") or {}
    for source in (groups, extras):
        for name, reqs in source.items():
            if name.lower() not in TEST_GROUPS:
                continue
            # entries may be {include-group: ...} tables; only strings install
            found += [r for r in reqs if isinstance(r, str)]
    return sorted(set(found))


def _make_has_test_target(tree: Path) -> bool:
    mk = tree / "Makefile"
    if not mk.is_file():
        return False
    try:
        return any(line.startswith(("test:", "test :", "check:"))
                   for line in mk.read_text(errors="replace").splitlines())
    except OSError:
        return False


def detect_suite(tree: Path) -> Suite | None:
    """The repo's own test setup, or None if we do not recognise it.

    Order matters: a Python project with a package.json for its docs site is
    still a Python project, so the language whose *tests* we can read per-test
    is checked first.
    """
    tree = Path(tree)

    py_marker = _has(tree, "pyproject.toml", "setup.py", "setup.cfg",
                     "requirements.txt", "pytest.ini", "tox.ini")
    if py_marker and _looks_like_python_tests(tree):
        setup = [["python", "-m", "pip", "install", "--quiet", "--upgrade", "pip"]]
        # Editable install first because it is what makes `import thepackage`
        # work; requirements as a fallback for repos that are not packages.
        if _has(tree, "pyproject.toml", "setup.py", "setup.cfg"):
            # Best effort, not a precondition. Plenty of working repos are not
            # pip-installable -- flat layouts with several top-level packages
            # make setuptools refuse to guess, and a project run entirely
            # through `uv run` never notices. pytest from the repo root still
            # imports them fine. If dependencies really are missing the suite
            # will fail to report, and `suite_reported` refuses on that.
            setup.append("python -m pip install --quiet -e . || true")
        for req in ("requirements.txt", "requirements-dev.txt",
                    "dev-requirements.txt", "test-requirements.txt"):
            if (tree / req).is_file():
                setup.append(["python", "-m", "pip", "install", "--quiet", "-r", req])
        # Whatever the repo says its own tests need. Best effort: a single
        # unresolvable pin should not stop the suite running at all.
        extra = test_requirements(tree)
        if extra:
            setup.append("python -m pip install --quiet "
                         + " ".join(_sh_safe(r) for r in extra) + " || true")
        setup.append(["python", "-m", "pip", "install", "--quiet", "pytest"])
        return Suite(
            kind="pytest",
            image=python_image(tree),
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

    # Last resort. A Makefile test target says the maintainers know how to run
    # their own suite, even when we cannot recognise the toolchain -- but it
    # tells us nothing about individual tests, so it caps at unverified.
    if _make_has_test_target(tree):
        return Suite(kind="make", image="debian:stable-slim", setup=[],
                     command=["make", "test"], per_test=False,
                     why="a Makefile with a test target",
                     marker_files=["Makefile"])

    return None


def refusal_reason(tree: Path) -> str:
    """Why a repo was refused, in words a person can act on."""
    tree = Path(tree)
    if _has(tree, *COMPOSE_FILES):
        return ("it uses Docker Compose, so its tests need other services "
                "(a database, a broker) standing up alongside them. Running a "
                "suite like that is not supported yet -- and guessing would "
                "produce a green run that never touched the code.")
    if _has(tree, "pyproject.toml", "setup.py", "setup.cfg", "requirements.txt"):
        return ("it looks like a Python project but I could not find any tests "
                "(no tests/ directory, no test_*.py, no *_test.py).")
    if (tree / "package.json").is_file():
        return "its package.json has no \"test\" script."
    return ("I looked for pytest, an npm test script, go.mod, Cargo.toml, and a "
            "Makefile test target, and found none of them.")
