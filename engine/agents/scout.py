"""Scout: turn a bug report into a context pack (FR-16).

Build week: 2. Read-only on the repo.

Deterministic on purpose. TAD §3.3 allows Scout an LLM tool loop, but the free
tier's 8000 tokens/minute ceiling means every token Scout spends is a token the
builder cannot have -- and search + AST already answer the question week 1
actually failed on ("where does this code live, and what does it really say").
An LLM scout is a later arm of E2, measured against this one, not a
prerequisite.

The pack is `[{file, start, end, why}]` under a hard token budget, exactly as
FR-16 specifies, and it carries the real source lines: week 1's builder kept
inventing context lines that did not exist in the file.

Emits: context_pack_ready.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass
from typing import Any
from pathlib import Path

from engine.repo import search
from engine.repo.repomap import (
    Symbol,
    enclosing_symbol,
    is_interesting,
    outline_file,
    read_span,
)

CHARS_PER_TOKEN = 4  # rough, deliberately pessimistic

_HEADER = (
    "Relevant code from the repository. Paths are repo-relative and the "
    "line numbers are real -- use them exactly as written.\n"
)
MAX_TERMS = 8
MAX_FILES = 4
SPAN_PAD = 4  # lines of breathing room around a symbol
DEFINITION_BONUS = 2.0  # defining a term beats mentioning it

# Identifiers worth searching for, in descending specificity.
_BACKTICKED = re.compile(r"`([^`\n]{2,80})`")
_DOTTED = re.compile(r"\b([a-z_][\w]*(?:\.[a-z_][\w]*){1,5})\b")
# Leading acronyms are everywhere in Python code -- ASCIIUsernameValidator,
# HTTPResponse, JSONEncoder, URLField. Requiring [A-Z][a-z]+ at the start
# makes every one of them invisible. Allow an uppercase run first, but
# still demand a later CamelCase segment so plain capitalised words
# ("Description", "However") do not qualify.
_CAMEL = re.compile(r"\b([A-Z][A-Za-z0-9]*(?:[A-Z][a-z0-9]+)+)\b")
_SNAKE = re.compile(r"\b([a-z][a-z0-9]*(?:_[a-z0-9]+)+)\b")

# Words that match half the codebase and rank nothing.
_STOP = {
    "self", "true", "false", "none", "return", "import", "from", "class",
    "def", "the", "this", "that", "value", "test", "tests", "error", "python",
    "django", "should", "would", "which", "there",
}


@dataclass(frozen=True)
class ContextEntry:
    """One span of real source, and why Scout thinks it matters."""

    file: str
    start: int
    end: int
    why: str
    source: str


# Above any evidence score: a cited file outranks a guessed one.
_CITED_SCORE = 1e9


def extract_terms(issue: str, limit: int = MAX_TERMS) -> list[str]:
    """Identifiers from the issue, most specific first.

    Order matters: `ASCIIUsernameValidator` locates the fix, `value` does not.
    """
    ranked: list[str] = []
    for pattern in (_BACKTICKED, _CAMEL, _DOTTED, _SNAKE):
        for match in pattern.finditer(issue):
            term = match.group(1).strip()
            if len(term) < 3 or term.lower() in _STOP:
                continue
            if any(ch in term for ch in " \t\n"):
                continue
            if term not in ranked:
                ranked.append(term)
    return ranked[:limit]


def _definition_of(term: str) -> re.Pattern[str]:
    """Matches the line that DEFINES `term`, not one that merely mentions it."""
    return re.compile(rf"^\s*(?:class|def)\s+{re.escape(term)}\b")


def _evidence(terms: list[str], tree: Path) -> dict[str, dict[str, Any]]:
    """Per-file evidence: which lines matched, how rare the term, was it a def.

    Rare terms carry the signal. A term hitting 60 files says almost nothing;
    one hitting 2 says almost everything. In django-10924 the issue names both
    `FilePathField` (the class being fixed, a handful of hits) and `CharField`
    (its base class, hundreds). Weighting them equally floods the pack with
    CharField's neighbours and never includes FilePathField at all -- the model
    replied that it could not see the code it was asked to change.
    """
    found_by_term = {}
    for term in terms:
        found = [h for h in search.search(term, tree, max_hits=60)
                 if is_interesting(h.path)]
        if found:
            found_by_term[term] = found

    files: dict[str, dict[str, Any]] = {}
    for term, found in found_by_term.items():
        weight = 1.0 / (1.0 + math.log(1 + len(found)))
        defines = _definition_of(term)
        for hit in found:
            bonus = DEFINITION_BONUS if defines.match(hit.text) else 0.0
            entry = files.setdefault(
                hit.path, {"score": 0.0, "hits": {}, "terms": []}
            )
            entry["score"] += weight + bonus
            # Keep the strongest weight seen for a line.
            entry["hits"][hit.line] = max(entry["hits"].get(hit.line, 0.0),
                                          weight + bonus)
            if term not in entry["terms"]:
                entry["terms"].append(term)
    return files


def _definition_of(term: str) -> re.Pattern[str]:
    """Matches the line that DEFINES `term`, not one that merely mentions it."""
    return re.compile(rf"^\s*(?:class|def)\s+{re.escape(term)}\b")


def rank_files(
    terms: list[str], tree: Path
) -> list[tuple[str, list[int], list[str]]]:
    """Files ordered by weighted evidence, strongest first."""
    files = _evidence(terms, tree)
    ordered = sorted(files.items(), key=lambda kv: kv[1]["score"], reverse=True)
    return [(path, sorted(e["hits"]), e["terms"]) for path, e in ordered]


def _spans_for(symbols: list[Symbol], lines: list[int]) -> list[tuple[int, int, str]]:
    """One span per distinct symbol the hits fall in, in file order.

    Returning only the tightest single symbol loses fixes that span siblings:
    django-11099 changes BOTH ASCIIUsernameValidator and
    UnicodeUsernameValidator, and a builder shown only the first fixes only
    the first -- which still fails the tests.
    """
    chosen: dict[str, tuple[int, int, str]] = {}
    loose: list[int] = []
    for line in lines:
        symbol = enclosing_symbol(symbols, line)
        if symbol is None:
            loose.append(line)
            continue
        chosen.setdefault(
            symbol.qualname, (symbol.start, symbol.end, f"{symbol.kind} {symbol.qualname}")
        )

    spans = sorted(chosen.values())
    if loose and not spans:
        lo, hi = min(loose), max(loose)
        spans = [(max(1, lo - SPAN_PAD), hi + SPAN_PAD, "matching lines")]
    return spans


def _rendered_cost(path: str, start: int, end: int, why: str, source: str) -> int:
    """Tokens one entry costs once wrapped for the prompt."""
    envelope = f"--- {path} (lines {start}-{end}) --- {why}\n```python\n\n```\n"
    return (len(source) + len(envelope)) // CHARS_PER_TOKEN


# A traceback names the file. A failing test's name usually names it too.
# Neither signal was used: extract_terms turns "engine/events.py:111" into
# ordinary words and ranks them by rarity across the repo, so the one place
# the answer was written down competed with every other mention of "events".
_PATH_IN_TEXT = re.compile(r"([\w./-]+\.py)(?::(\d+))?")
_TEST_FILE = re.compile(r"(?:^|/)test_([\w]+)\.py")


def cited_paths(issue: str, tree: Path) -> list[tuple[str, int | None]]:
    """Files the failing output points at, in the order it points at them.

    Two signals, both free:

      * a path in a traceback -- `engine/events.py:111` -- which is the file
        that actually raised, and usually the file to fix.
      * a failing test's name -- `tests/test_config.py` implies `config.py`
        somewhere in the tree. Convention, but a strong one: every file this
        missed on the garage set had a same-named test.

    Tests themselves are never cited: the fix does not belong in them, and
    offering them invites a patch that edits the test instead.
    """
    out: list[tuple[str, int | None]] = []
    seen: set[str] = set()

    def add(rel: str, line: int | None) -> None:
        if rel in seen or is_test_path(rel) or not (Path(tree) / rel).is_file():
            return
        seen.add(rel)
        out.append((rel, line))

    for m in _PATH_IN_TEXT.finditer(issue):
        add(m.group(1).lstrip("./"), int(m.group(2)) if m.group(2) else None)

    # tests/test_config.py -> **/config.py
    for m in _TEST_FILE.finditer(issue):
        stem = m.group(1)
        for cand in sorted(Path(tree).rglob(f"{stem}.py")):
            add(str(cand.relative_to(tree)), None)
    return out


def is_test_path(rel: str) -> bool:
    name = Path(rel).name
    return (name.startswith("test_") or name.endswith("_test.py")
            or name == "conftest.py" or "tests" in Path(rel).parts)


def build_context_pack(
    issue: str, tree: Path, token_budget: int, max_files: int = MAX_FILES
) -> list[ContextEntry]:
    """Files and line spans most likely to hold the fix, under `token_budget`.

    Candidate spans from every promising file compete on evidence before the
    budget is spent. Filling the budget file-by-file in line order spends it on
    whatever happens to sit near the top of a 2000-line module -- which is how
    the class being fixed ends up cut from its own context pack.
    """
    terms = extract_terms(issue)
    cited = cited_paths(issue, tree)
    if not terms and not cited:
        return []

    files = _evidence(terms, tree) if terms else {}
    ranked = sorted(files.items(), key=lambda kv: kv[1]["score"], reverse=True)

    candidates: list[tuple[float, str, int, int, str, list[str]]] = []
    for path, entry in ranked[:max_files]:
        symbols = outline_file(tree, path)
        lines = sorted(entry["hits"])
        for start, end, what in _spans_for(symbols, lines):
            score = sum(w for line, w in entry["hits"].items() if start <= line <= end)
            candidates.append((score, path, start, end, what, entry["terms"]))

    # Cited files go to the front, ahead of everything ranked by evidence. A
    # file the traceback named is not a guess.
    for rank, (rel, line) in enumerate(cited):
        symbols = outline_file(tree, rel)
        spans = _spans_for(symbols, [line]) if line else _spans_for(symbols, [])
        if not spans:
            spans = [(1, 60, "cited by the failing output")]
        why = f"named at line {line} in the failing output" if line else \
              "matches the name of a failing test"
        for start, end, what in spans[:2]:
            candidates.append((_CITED_SCORE - rank, rel, start, end,
                               what or why, ["<cited>"]))

    candidates.sort(key=lambda c: c[0], reverse=True)

    pack: list[ContextEntry] = []
    spent = len(_HEADER) // CHARS_PER_TOKEN  # the pack's own preamble
    for _, path, start, end, what, matched in candidates:
        source = read_span(tree, path, start, end)
        if not source:
            continue
        cost = _rendered_cost(path, start, end, what, source)
        if spent + cost > token_budget:
            continue  # a later, smaller span may still fit
        spent += cost
        pack.append(
            ContextEntry(
                file=path, start=start, end=end,
                why=f"{what}; matches {', '.join(matched[:3])}",
                source=source,
            )
        )
    # Present in file order: easier to read, and the builder quotes line numbers.
    pack.sort(key=lambda e: (e.file, e.start))
    return pack


def render_pack(pack: list[ContextEntry]) -> str:
    """The pack as the builder sees it: real paths, real line numbers, real code."""
    if not pack:
        return ""
    blocks = [_HEADER]
    for entry in pack:
        blocks.append(
            f"--- {entry.file} (lines {entry.start}-{entry.end}) --- {entry.why}\n"
            f"```python\n{entry.source}\n```"
        )
    return "\n".join(blocks)
