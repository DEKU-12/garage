"""Builder agent: bug report + context -> a unified diff. Prompt: prompts/builder.md.

Build week: 1. Contract: TAD §3.3 -- the strictest in the system, because the
output goes straight to `git apply`.

The builder never ships. It writes a candidate; the gates decide. On a retry it
is handed the previous failure verbatim (git's stderr, or the failing test
output) -- specific feedback is the entire mechanism by which attempt N+1 beats
attempt N.

Emits: patch_produced (via the caller, once events.py lands).
"""

from __future__ import annotations

from pathlib import Path

from engine.agents.base import Usage, call_model
from engine.agents.stub import StubBackend
from engine.config import RunConfig

PROMPTS = Path(__file__).resolve().parents[1] / "prompts"


def _system_prompt() -> str:
    return (PROMPTS / "builder.md").read_text()


def build_user_message(issue: str, context: str, feedback: str | None = None) -> str:
    """Assemble what the builder sees for one attempt."""
    parts = [f"## Bug report\n\n{issue.strip()}"]
    if context.strip():
        parts.append(f"## Relevant source\n\n{context.strip()}")
    if feedback:
        parts.append(
            "## Your previous attempt failed\n\n"
            f"{feedback.strip()}\n\n"
            "Fix the cause of this failure. If the error is about the diff "
            "itself (bad paths, context that does not match), re-copy the "
            "context lines exactly from the source above."
        )
    return "\n\n".join(parts)


def build_patch(
    issue: str,
    context: str,
    cfg: RunConfig,
    stub: StubBackend | None = None,
    feedback: str | None = None,
) -> tuple[str, str, Usage]:
    """One builder attempt. Returns (raw_response, user_message, usage).

    The raw response is returned unparsed on purpose: extraction belongs to
    repo/patch.py, and the raw text is an artifact we persist for replay (FR-6).
    """
    user = build_user_message(issue, context, feedback)
    messages = [
        {"role": "system", "content": _system_prompt()},
        {"role": "user", "content": user},
    ]
    text, usage = call_model("builder", messages, cfg, stub=stub)
    return text, user, usage
