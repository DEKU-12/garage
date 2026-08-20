"""Scout: term extraction, ranking, spans, and the context pack (FR-16).

Offline: a small fake repo in tmp_path. Needs ripgrep on PATH (rules.md §2.1
makes rg a hard dependency of the search channel), nothing else.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from engine.agents.scout import (
    build_context_pack,
    extract_terms,
    rank_files,
    render_pack,
)
from engine.repo import search
from engine.repo.repomap import enclosing_symbol, outline, read_span

pytestmark = pytest.mark.skipif(not search.available(), reason="ripgrep not installed")

SOURCE = '''\
"""Validators."""


class ASCIIUsernameValidator:
    """Only ASCII."""

    regex = r'^[\\w.@+-]+$'
    flags = 1


class UnicodeUsernameValidator:
    """Anything unicode."""

    regex = r'^[\\w.@+-]+$'
    flags = 0


def helper(value):
    return value
'''


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    pkg = tmp_path / "app" / "auth"
    pkg.mkdir(parents=True)
    (pkg / "validators.py").write_text(SOURCE)
    (pkg / "models.py").write_text("class User:\n    pass\n")
    # A test file and a migration: both must be ignored by ranking.
    tests = tmp_path / "app" / "tests"
    tests.mkdir()
    (tests / "test_validators.py").write_text("ASCIIUsernameValidator\n" * 5)
    return tmp_path


# --- term extraction ------------------------------------------------------

def test_extracts_identifiers_most_specific_first() -> None:
    issue = "ASCIIUsernameValidator and UnicodeUsernameValidator use `r'^[\\w.@+-]+$'`"
    terms = extract_terms(issue)
    assert "ASCIIUsernameValidator" in terms
    assert "UnicodeUsernameValidator" in terms


def test_drops_noise_words() -> None:
    assert extract_terms("the value should return none") == []


# --- ranking --------------------------------------------------------------

def test_ranks_by_number_of_distinct_terms(repo: Path) -> None:
    ranked = rank_files(["ASCIIUsernameValidator", "UnicodeUsernameValidator"], repo)
    assert ranked, "expected at least one ranked file"
    assert ranked[0][0] == "app/auth/validators.py"


def test_ranking_ignores_test_files(repo: Path) -> None:
    """A test file mentioning the symbol 5 times must not outrank the source."""
    paths = [path for path, _, _ in rank_files(["ASCIIUsernameValidator"], repo)]
    assert not any("tests/" in p for p in paths)


# --- spans ----------------------------------------------------------------

def test_enclosing_symbol_picks_the_narrowest() -> None:
    symbols = outline(SOURCE)
    got = enclosing_symbol(symbols, 7)  # the regex line in the ASCII class
    assert got is not None and got.name == "ASCIIUsernameValidator"


def test_read_span_returns_real_lines() -> None:
    assert "class ASCIIUsernameValidator" in SOURCE
    # span is 1-based inclusive
    lines = SOURCE.splitlines()
    assert lines[3] == "class ASCIIUsernameValidator:"


def test_read_span_off_a_file(repo: Path) -> None:
    got = read_span(repo, "app/auth/validators.py", 4, 8)
    assert got.startswith("class ASCIIUsernameValidator:")
    assert "regex" in got


# --- the pack -------------------------------------------------------------

def test_pack_covers_every_matching_symbol_not_just_one(repo: Path) -> None:
    """django-11099 needs BOTH validator classes; one span would half-fix it."""
    issue = "ASCIIUsernameValidator and UnicodeUsernameValidator use the wrong regex"
    pack = build_context_pack(issue, repo, token_budget=2000)
    names = " ".join(e.why for e in pack)
    assert "ASCIIUsernameValidator" in names
    assert "UnicodeUsernameValidator" in names


def test_pack_respects_the_token_budget(repo: Path) -> None:
    issue = "ASCIIUsernameValidator and UnicodeUsernameValidator"
    pack = build_context_pack(issue, repo, token_budget=5)
    assert sum(len(e.source) // 4 for e in pack) <= 5


def test_pack_is_empty_when_the_issue_names_nothing(repo: Path) -> None:
    assert build_context_pack("it is broken please fix", repo, 2000) == []


def test_render_states_that_paths_and_lines_are_real(repo: Path) -> None:
    """Week 1's builder invented paths and context lines; the header says not to."""
    pack = build_context_pack("ASCIIUsernameValidator is wrong", repo, 2000)
    rendered = render_pack(pack)
    assert "repo-relative" in rendered
    assert "app/auth/validators.py" in rendered


def test_render_of_an_empty_pack_is_empty() -> None:
    assert render_pack([]) == ""


# ------------------------------------------- the answer written in the input

def _tree(tmp_path, **files):
    for rel, text in files.items():
        p = tmp_path / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text)
    return tmp_path


def test_a_path_in_a_traceback_is_used(tmp_path):
    """Half the garage mutants named the broken file in the failing output,
    and Scout ignored it: extract_terms turned `engine/events.py:111` into
    ordinary words and ranked them by rarity against the whole repo."""
    from engine.agents.scout import cited_paths

    _tree(tmp_path, **{"engine/events.py": "def emit():\n    pass\n"})
    got = cited_paths('  File "engine/events.py", line 111, in emit\n', tmp_path)
    assert ("engine/events.py", 111) in got


def test_a_failing_tests_name_points_at_its_module(tmp_path):
    """tests/test_config.py implies config.py. Convention, but every file
    Scout missed on the garage set had a same-named test."""
    from engine.agents.scout import cited_paths

    _tree(tmp_path, **{"engine/config.py": "X = 1\n",
                       "tests/test_config.py": "def test_x(): pass\n"})
    got = [p for p, _ in cited_paths("FAILED tests/test_config.py::test_x", tmp_path)]
    assert "engine/config.py" in got


def test_tests_are_never_cited(tmp_path):
    """Offering the test file invites a patch that edits the test."""
    from engine.agents.scout import cited_paths

    _tree(tmp_path, **{"tests/test_config.py": "def test_x(): pass\n",
                       "engine/config.py": "X = 1\n"})
    got = [p for p, _ in cited_paths("FAILED tests/test_config.py::test_x", tmp_path)]
    assert "tests/test_config.py" not in got


def test_a_cited_file_that_does_not_exist_is_ignored(tmp_path):
    from engine.agents.scout import cited_paths

    _tree(tmp_path, **{"real.py": "X = 1\n"})
    assert cited_paths("File \"imaginary/gone.py\", line 3", tmp_path) == []


def test_the_cited_file_reaches_the_pack(tmp_path):
    """Ranked above everything scored by evidence: a file the traceback named
    is not a guess."""
    from engine.agents.scout import build_context_pack

    _tree(tmp_path,
          **{"engine/events.py": "def emit(kind):\n    return kind\n",
             "noise.py": "\n".join(f"# emit emit emit {i}" for i in range(200))})
    pack = build_context_pack('File "engine/events.py", line 2, in emit',
                              tmp_path, token_budget=6000)
    assert "engine/events.py" in {e.file for e in pack}
