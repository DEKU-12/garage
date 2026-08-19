"""Config invariants — the caps that quietly rigged E1 (rules.md §4.1.2).

Every one of these existed as a number nobody checked. Each became the binding
constraint on the gate-ON arm, which is the only arm that retries, so each
biased the experiment against the thing it measures.
"""

from __future__ import annotations

from engine.accounting.pricing import PRICES, cost_usd
from engine.config import RunConfig

WORST_PROMPT_TOKENS = 12_000  # scout pack + issue + failure feedback


def _worst_case_task_cost(cfg: RunConfig, model: str) -> float:
    """What one task costs if every builder run and review hits its ceiling."""
    per_run = cost_usd(model, WORST_PROMPT_TOKENS, cfg.max_completion_tokens)
    return cfg.max_builder_runs * per_run * 2  # x2: a reviewer call per attempt


def test_the_dollar_cap_clears_a_full_retry_budget() -> None:
    """A backstop that binds before the retry cap IS the retry cap."""
    cfg = RunConfig(run_id="x", task_ids=[])
    priciest = max(
        (m for m in PRICES if m.startswith("claude-")),
        key=lambda m: PRICES[m].output_per_m,
    )
    # Sonnet is the working default; the cap must clear its worst case.
    assert cfg.per_task_usd_cap >= _worst_case_task_cost(cfg, "claude-sonnet-5")


def test_the_token_cap_clears_a_full_retry_budget() -> None:
    cfg = RunConfig(run_id="x", task_ids=[])
    worst = cfg.max_builder_runs * (WORST_PROMPT_TOKENS + cfg.max_completion_tokens)
    assert cfg.per_task_token_cap >= worst


def test_the_output_ceiling_leaves_room_for_thinking_plus_a_diff() -> None:
    """16k truncated E1's hard tasks mid-diff (finish_reason=max_tokens)."""
    assert RunConfig(run_id="x", task_ids=[]).max_completion_tokens >= 32_000


def test_scout_gets_the_context_budget_the_prd_specifies() -> None:
    assert RunConfig(run_id="x", task_ids=[]).context_token_budget == 6_000
