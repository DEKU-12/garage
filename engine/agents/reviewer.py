"""Reviewer: the simplicity gate, applying the ponytail ladder (FR-9).

Build week: 2. Prompt: engine/prompts/reviewer.md (versioned by hash).

Authority: can REJECT a patch for being over-engineered, max 1 retry. It can
never reject for correctness -- the tester already ruled on that.

ADR-7: at the retry cap a rejected-but-correct patch SHIPS ANYWAY. A patch that
passes the tests is never discarded for style; the verdict is still recorded,
which is what E3 measures.

A malformed verdict is treated as ACCEPT and logged (TAD §3.3): the reviewer is
the junior gate, and an unparseable opinion must never block a correct patch.

Emits: gate_verdict.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from engine.agents.base import Usage, call_model, load_prompt
from engine.config import RunConfig
from engine.agents.stub import StubBackend

_ACCEPT = re.compile(r"^\s*ACCEPT\b", re.IGNORECASE)
_REJECT = re.compile(
    r"^\s*REJECT\s*/\s*rung\s*(?P<rung>[1-6])\s*/\s*(?P<reason>.+)",
    re.IGNORECASE | re.DOTALL,
)


@dataclass(frozen=True)
class Review:
    """One simplicity verdict."""

    verdict: str  # "accept" | "reject"
    rung: int | None
    reason: str
    parse_warning: bool  # the model's reply did not match the contract
    raw: str


def parse_verdict(text: str) -> Review:
    """Parse the reviewer's reply. Never raises -- unparseable means ACCEPT."""
    stripped = (text or "").strip()

    if _ACCEPT.match(stripped):
        return Review("accept", None, "", False, stripped)

    match = _REJECT.match(stripped)
    if match:
        return Review(
            "reject",
            int(match.group("rung")),
            " ".join(match.group("reason").split())[:800],
            False,
            stripped,
        )

    # Anything else: the model ignored the contract. Treat as ACCEPT and say so.
    return Review("accept", None, "", True, stripped)


def review_patch(
    diff: str, cfg: RunConfig, stub: StubBackend | None = None
) -> tuple[Review, str, Usage]:
    """Ask the reviewer whether the patch is bigger than it needs to be."""
    system = load_prompt("reviewer")
    user = (
        "Review this patch for simplicity only.\n\n"
        f"```diff\n{diff}\n```"
    )
    messages = [
        {"role": "system", "content": system},
        {"role": "user", "content": user},
    ]
    raw, usage = call_model("reviewer", messages, cfg, stub)
    return parse_verdict(raw), user, usage
