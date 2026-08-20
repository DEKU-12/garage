"""The mutation engine: breaking code on purpose, correctly.

A mutation harness that produces bad tasks is worse than none: every bad task
silently moves the number, and nothing about the output looks wrong.
"""

from __future__ import annotations

import ast

import pytest

from engine.eval.mutate import candidates, generate, is_source_file, make


def write(tmp_path, rel, text):
    p = tmp_path / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text)
    return p


# --------------------------------------------------------- what may be broken

def test_vendored_code_is_never_mutated():
    """The first run of this mutated pytest itself.

    Every candidate came from `.venv/lib/python3.12/site-packages/_pytest/`,
    because generate() walked every .py under the tree. It would have asked
    the model to repair a bug injected into its own test runner.
    """
    for rel in (
        ".venv/lib/python3.12/site-packages/_pytest/_code/code.py",
        "venv/lib/python3.11/site-packages/anything.py",
        "node_modules/x/y.py",
        "build/lib/thing.py",
        ".tox/py311/lib/pkg.py",
        "src/thing.egg-info/x.py",
        "vendor/lib.py",
    ):
        assert not is_source_file(rel), rel


def test_tests_are_never_mutated():
    """A 'fix' must not be able to be deleting the test that caught it."""
    for rel in ("tests/test_x.py", "test_thing.py", "thing_test.py",
                "conftest.py", "src/tests/helper.py"):
        assert not is_source_file(rel), rel


def test_ordinary_source_is_mutable():
    for rel in ("engine/graph.py", "src/t2sql/guardrails.py", "app.py"):
        assert is_source_file(rel), rel


# ------------------------------------------------------------- the mutation

def test_a_mutant_is_still_valid_python(tmp_path):
    """A file that will not parse is not a bug, it is a broken file, and
    scoring a model against it measures nothing."""
    write(tmp_path, "m.py", "def f(a, b):\n    if a > b and a != 0:\n        return True\n    return False\n")
    made = [make(tmp_path, "m.py", s) for s in candidates(tmp_path, "m.py")]
    made = [m for m in made if m]
    assert made
    for m in made:
        ast.parse(m.source)              # raises if the mutation broke syntax


def test_a_mutation_actually_changes_something(tmp_path):
    write(tmp_path, "m.py", "def f(a, b):\n    return a >= b\n")
    (m,) = [x for x in (make(tmp_path, "m.py", s)
                        for s in candidates(tmp_path, "m.py")) if x]
    assert m.before != m.after
    assert m.source != (tmp_path / "m.py").read_text()
    assert m.before.strip() == "return a >= b"


def test_only_the_targeted_line_changes(tmp_path):
    """Going through ast.unparse would reformat the whole file and drop every
    comment, burying a one-character bug in a thousand-line diff."""
    src = ('# a comment that must survive\ndef f(a, b):\n'
           '    """docstring with > and and inside"""\n'
           '    return a > b\n')
    write(tmp_path, "m.py", src)
    made = [x for x in (make(tmp_path, "m.py", s)
                        for s in candidates(tmp_path, "m.py")) if x]
    assert made
    for m in made:
        before, after = src.splitlines(), m.source.splitlines()
        assert len(before) == len(after)
        differing = [i for i, (x, y) in enumerate(zip(before, after)) if x != y]
        assert differing == [m.line - 1]
        assert "# a comment that must survive" in m.source
        assert "docstring with > and and inside" in m.source


def test_the_name_main_guard_is_left_alone(tmp_path):
    """Flipping it runs a script on import, breaking the suite at collection
    time for reasons unrelated to any logic under test."""
    write(tmp_path, "m.py", 'if __name__ == "__main__":\n    print(1)\n')
    assert [m for m in (make(tmp_path, "m.py", s)
                        for s in candidates(tmp_path, "m.py")) if m] == []


# ------------------------------------------------------------ the mutant set

def test_the_same_seed_gives_the_same_set(tmp_path):
    """Experiments must run the same bugs, or E2 and E3 cannot be compared."""
    for i in range(4):
        write(tmp_path, f"pkg/m{i}.py", f"def f(a, b):\n    return a > b and a != {i}\n")
    a = [m.mid for m in generate(tmp_path, limit=6, seed=7)]
    b = [m.mid for m in generate(tmp_path, limit=6, seed=7)]
    c = [m.mid for m in generate(tmp_path, limit=6, seed=8)]
    assert a == b and a != []
    assert a != c


def test_mutants_are_spread_across_files(tmp_path):
    """Fifty mutations of one hot function would measure one function."""
    for i in range(5):
        write(tmp_path, f"pkg/m{i}.py",
              "def f(a, b):\n    return a > b and a != 0 or a <= b\n")
    ms = generate(tmp_path, limit=5, seed=1)
    assert len({m.path for m in ms}) == 5


def test_generating_from_a_repo_with_nothing_to_break_is_empty(tmp_path):
    write(tmp_path, "m.py", "X = 1\n")
    assert generate(tmp_path, limit=5) == []
