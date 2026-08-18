"""The single model call path: call_model(role, messages, cfg) -> (text, Usage).

Build week: 1. No agent calls the API directly (rules.md §2.1) -- one wrapper
means one place for retries, timeouts, usage accounting, and the stub.

Failure handling (rules.md §3.1): retry with exponential backoff + jitter on
transient faults, max 3 attempts, 120 s timeout per call. On final failure
raise ModelCallError, which is caught at the per-task boundary. Non-transient
errors (bad request, auth) fail immediately -- retrying them just burns time.

Emits: nothing directly; usage goes to accounting/ledger.py (cost_tick).
"""

from __future__ import annotations

import os
import random
import time
from dataclasses import dataclass
from typing import Any

import openai

from engine.agents.stub import StubBackend
from engine.config import STUB_MODEL, RunConfig
from engine.errors import ModelCallError

GROQ_BASE_URL = "https://api.groq.com/openai/v1"
REQUEST_TIMEOUT_S = 120.0
MAX_ATTEMPTS = 3
BACKOFF_BASE_S = 1.5

# Faults worth retrying: the service was busy or unreachable, not wrong.
TRANSIENT = (
    openai.APITimeoutError,
    openai.APIConnectionError,
    openai.RateLimitError,
    openai.InternalServerError,
)


@dataclass(frozen=True)
class Usage:
    """What one model call cost, in tokens and time. Priced later by pricing.py."""

    model: str
    role: str
    prompt_tokens: int
    completion_tokens: int
    latency_ms: int

    @property
    def total_tokens(self) -> int:
        return self.prompt_tokens + self.completion_tokens


def _client() -> openai.OpenAI:
    """Groq through its OpenAI-compatible endpoint."""
    key = os.environ.get("GROQ_API_KEY")
    if not key:
        raise ModelCallError(
            "GROQ_API_KEY is not set. Put it in .env, or run with --model stub "
            "to exercise the pipeline offline."
        )
    return openai.OpenAI(base_url=GROQ_BASE_URL, api_key=key, timeout=REQUEST_TIMEOUT_S)


def _sleep_for(attempt: int) -> float:
    """Exponential backoff with jitter, so parallel retries don't sync up."""
    return BACKOFF_BASE_S**attempt * (0.5 + random.random())


def call_model(
    role: str,
    messages: list[dict[str, str]],
    cfg: RunConfig,
    stub: StubBackend | None = None,
) -> tuple[str, Usage]:
    """Call the model backing `role` and return its text plus usage.

    `stub` is passed in explicitly rather than reached for globally, so a stub
    run is a property of the call site, not of the process.
    """
    model = cfg.model_for(role)
    started = time.monotonic()

    if model == STUB_MODEL:
        if stub is None:
            raise ModelCallError(
                f"role {role!r} is configured for the stub model but no "
                "StubBackend was passed to call_model"
            )
        text = stub.next_response(role)
        return text, Usage(
            model=STUB_MODEL,
            role=role,
            prompt_tokens=0,
            completion_tokens=0,
            latency_ms=int((time.monotonic() - started) * 1000),
        )

    last: Exception | None = None
    for attempt in range(1, MAX_ATTEMPTS + 1):
        try:
            response = _client().chat.completions.create(
                model=model,
                messages=messages,  # type: ignore[arg-type]
                temperature=cfg.temperature,
                seed=cfg.seed,
            )
        except TRANSIENT as exc:
            last = exc
            if attempt < MAX_ATTEMPTS:
                time.sleep(_sleep_for(attempt))
            continue
        except openai.APIStatusError as exc:
            # 4xx that isn't rate limiting: the request is wrong. Retrying it
            # will be wrong three times instead of once.
            raise ModelCallError(f"{role}/{model}: {exc.status_code} {exc.message}") from exc

        text = (response.choices[0].message.content or "").strip()
        return text, _usage_from(response, model, role, started)

    raise ModelCallError(
        f"{role}/{model}: failed after {MAX_ATTEMPTS} attempts: {last}"
    ) from last


def _usage_from(response: Any, model: str, role: str, started: float) -> Usage:
    """Read the API's own usage block; absent fields count as zero, never guessed."""
    usage = getattr(response, "usage", None)
    return Usage(
        model=model,
        role=role,
        prompt_tokens=getattr(usage, "prompt_tokens", 0) or 0,
        completion_tokens=getattr(usage, "completion_tokens", 0) or 0,
        latency_ms=int((time.monotonic() - started) * 1000),
    )
