"""RunConfig: models per role, gate flags, retry caps, budgets, seed (TAD §7).

Build week: 1. Pydantic v2, validated at the edge and trusted internally.

Every experiment in PRD §10 is exactly ONE config file -- no code changes
between the arms of an A/B. `prompt_hashes` is what makes that checkable after
the fact: the SHA-256 of every prompt file goes into the run's frozen
config.json, so "did the prompt change between these two runs" is never a
matter of memory (NFR-1, TAD §6).
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

from pydantic import BaseModel, Field

ROLES = ("orchestrator", "scout", "builder", "tester", "reviewer", "scribe")

STUB_MODEL = "stub"


class RunConfig(BaseModel):
    """Frozen description of one run. Written to runs/<run_id>/config.json."""

    run_id: str
    task_ids: list[str] = Field(default_factory=list)
    model_for_role: dict[str, str] = Field(default_factory=dict)

    # Gates (FR-10). Wired as graph edges, not `if`s inside nodes.
    tester_gate: bool = True
    reviewer_gate: bool = True
    scout: bool = True

    # Caps -- together these bound the builder at 1 + 3 + 1 = 5 runs per task.
    max_correctness_retries: int = 3
    max_simplicity_retries: int = 1

    # FR-12: a token cap needs no price table, so budgets work from day one.
    # Checked at node entry, never mid-call: a task ends `budget_exceeded`,
    # which is a clean stop, not a crash.
    per_task_token_cap: int = 40_000

    # Deterministic repair of model hunk-header arithmetic (R3). A flag,
    # not a constant, so its contribution can be measured like any other.
    repair_hunks: bool = True

    # PRD says 6000. That cannot fit: the free tier's 8000 tokens/minute must
    # hold prompt + context + reserved answer, and max_completion_tokens alone
    # is 6000. 1500 leaves room for all three. Raise this the moment the
    # account moves off the free tier -- it is a tier limit, not a design one.
    context_token_budget: int = 1500
    scout_max_tool_calls: int = 6

    # Budgets (FR-12). Hitting one is `budget_exceeded`, never `crashed`.
    per_task_usd_cap: float = 0.50
    per_run_usd_cap: float = 15.00

    temperature: float = 0.2
    seed: int | None = 7

    # Reasoning models (openai/gpt-oss-*) spend completion tokens THINKING
    # before they emit any content. Groq's default cap is 2048, which the
    # 20b model exhausts on reasoning alone for a real SWE-bench issue --
    # returning empty content while reporting a full 2048 completion tokens.
    # An explicit budget plus low effort keeps the answer inside the cap:
    # we want a diff, not an essay.
    # Groq's free tier caps TOKENS PER MINUTE at 8000 for gpt-oss-20b, and it
    # counts max_completion_tokens toward the request size -- so
    # prompt + max_completion_tokens must stay under 8000 or every call 413s
    # before the model sees it. 6000 leaves ~2000 for prompt + context, which
    # also caps context_token_budget on this tier (PRD's 6000 will not fit).
    max_completion_tokens: int = 6000
    reasoning_effort: str = "low"  # "low" | "medium" | "high"

    prompt_hashes: dict[str, str] = Field(default_factory=dict)

    @property
    def max_builder_runs(self) -> int:
        """The termination proof, as a number: 1 + 3 + 1 = 5 (TAD §3.2)."""
        return 1 + self.max_correctness_retries + self.max_simplicity_retries

    def model_for(self, role: str) -> str:
        """The model backing `role`, defaulting to the stub so nothing spends
        money by accident."""
        return self.model_for_role.get(role, STUB_MODEL)

    @property
    def is_stub_run(self) -> bool:
        """True when no role talks to a real API.

        Stub runs exercise the pipeline; they are NOT results. Nothing derived
        from one may reach a report (rules.md §4.1.1).
        """
        return all(self.model_for(r) == STUB_MODEL for r in ROLES)

    def freeze_to(self, path: Path) -> None:
        """Write the config as this run's immutable provenance record."""
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(self.model_dump(), indent=2, sort_keys=True))


def prompt_hashes(prompts_dir: Path) -> dict[str, str]:
    """SHA-256 of every prompt file, keyed by role name."""
    return {
        p.stem: hashlib.sha256(p.read_bytes()).hexdigest()
        for p in sorted(Path(prompts_dir).glob("*.md"))
        if p.stem != "README"
    }
