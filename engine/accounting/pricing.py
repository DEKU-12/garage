"""Per-model price table and the one function that turns tokens into dollars.

Build week: 2. FR-32.

Prices are FACTS, copied from the provider's own documentation with the date
and URL recorded. They are never estimated, never rounded "close enough", and
never inferred from a bill -- $/solved (M2) is a headline metric and rules.md
§0.3 applies to its inputs as much as its output.

Sources, fetched 2026-08-18:
  https://console.groq.com/docs/model/openai/gpt-oss-20b
  https://console.groq.com/docs/model/openai/gpt-oss-120b

An unknown model does NOT price at zero. Zero would silently understate
$/solved and look like a bargain; unpriced calls are counted and surfaced so a
report can say "cost unknown for N calls" instead of quietly lying.
"""

from __future__ import annotations

from dataclasses import dataclass

PRICES_FETCHED = "2026-08-18"


@dataclass(frozen=True)
class Price:
    """USD per 1,000,000 tokens."""

    input_per_m: float
    output_per_m: float
    cached_input_per_m: float | None = None


# Keyed by the exact model id sent to the API.
PRICES: dict[str, Price] = {
    "openai/gpt-oss-20b": Price(0.075, 0.30, 0.037),
    "openai/gpt-oss-120b": Price(0.15, 0.60, 0.075),
    # Anthropic, from platform.claude.com/docs pricing, fetched 2026-08-19.
    # Sonnet 5 is on introductory pricing ($2/$10) through 2026-08-31; list
    # price is $3/$15. The intro figure is what runs before that date actually
    # cost, so it is what the ledger should price them at -- change this entry
    # on Sept 1 rather than back-dating old runs.
    "claude-sonnet-5": Price(2.00, 10.00),
    "claude-opus-5": Price(5.00, 25.00),
    "claude-haiku-4-5": Price(1.00, 5.00),
    # The stub calls nothing and costs nothing. This is a real zero, not a
    # missing entry -- which is why it is listed rather than left unknown.
    "stub": Price(0.0, 0.0, 0.0),
}


def is_priced(model: str) -> bool:
    return model in PRICES


def cost_usd(model: str, prompt_tokens: int, completion_tokens: int) -> float:
    """Dollars for one call. Unknown models cost 0.0 -- check is_priced() too.

    Callers that report money must count unpriced calls separately; see
    report/aggregate.py, which refuses to print a $/solved figure when any
    contributing call was unpriced.
    """
    price = PRICES.get(model)
    if price is None:
        return 0.0
    return (
        prompt_tokens / 1_000_000 * price.input_per_m
        + completion_tokens / 1_000_000 * price.output_per_m
    )
