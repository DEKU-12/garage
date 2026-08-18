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

import re
from dataclasses import dataclass
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
MAX_TERMS = 8
MAX_FILES = 4
SPAN_PAD = 4  # lines of breathing room around a symbol

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


def rank_files(terms: list[str], tree: Path) -> list[tuple[str, list[int], list[str]]]:
    """Files ordered by how many distinct issue terms appear in them.

    Agreement across terms is the signal: a file matching four identifiers from
    the report is far more likely to hold the fix than one matching a single
    common word.
    """
    per_file: dict[str, tuple[set[int], list[str]]] = {}
    for term in terms:
        for hit in search.search(term, tree, max_hits=25):
            if not is_interesting(hit.path):
                continue
            lines, matched = per_file.setdefault(hit.path, (set(), []))
            lines.add(hit.line)
            if term not in matched:
                matched.append(term)

    ordered = sorted(
        per_file.items(), key=lambda kv: (len(kv[1][1]), -len(kv[1][0])), reverse=True
    )
    return [(path, sorted(lines), matched) for path, (lines, matched) in ordered]


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


def build_context_pack(
    issue: str, tree: Path, token_budget: int, max_files: int = MAX_FILES
) -> list[ContextEntry]:
    """Files and line spans most likely to hold the fix, under `token_budget`."""
    terms = extract_terms(issue)
    if not terms:
        return []

    pack: list[ContextEntry] = []
    spent = 0
    for path, lines, matched in rank_files(terms, tree)[:max_files]:
        symbols = outline_file(tree, path)
        for start, end, what in _spans_for(symbols, lines):
            source = read_span(tree, path, start, end)
            if not source:
                continue

            cost = len(source) // CHARS_PER_TOKEN
            if spent + cost > token_budget:
                continue  # a later, smaller span may still fit
            spent += cost
            pack.append(
                ContextEntry(
                    file=path,
                    start=start,
                    end=end,
                    why=f"{what}; matches {', '.join(matched[:3])}",
                    source=source,
                )
            )
    return pack


def render_pack(pack: list[ContextEntry]) -> str:
    """The pack as the builder sees it: real paths, real line numbers, real code."""
    if not pack:
        return ""
    blocks = [
        "Relevant code from the repository. Paths are repo-relative and the "
        "line numbers are real -- use them exactly as written.\n"
    ]
    for entry in pack:
        blocks.append(
            f"--- {entry.file} (lines {entry.start}-{entry.end}) --- {entry.why}\n"
            f"```python\n{entry.source}\n```"
        )
    return "\n".join(blocks)
