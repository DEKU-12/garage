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
from pathlib import Path
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
    finish_reason: str = ""  # "stop" | "length" | ... -- makes truncation visible

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


PROMPTS = Path(__file__).resolve().parents[1] / "prompts"


def load_prompt(role: str) -> str:
    """The system prompt for `role`, read from engine/prompts/<role>.md.

    One loader for every agent: prompts are versioned artifacts whose hashes go
    into config.json (NFR-1), so exactly one place should know where they live.
    """
    return (PROMPTS / f"{role}.md").read_text(encoding="utf-8")


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
        # Estimated, not measured -- but zero would make budget caps (FR-12)
        # impossible to exercise without spending money, and a stub run that
        # cannot reach a cap cannot test the code that enforces it. Stub runs
        # are already flagged as non-results, so a rough number is honest here.
        prompt_chars = sum(len(str(m.get("content", ""))) for m in messages)
        return text, Usage(
            model=STUB_MODEL,
            role=role,
            prompt_tokens=prompt_chars // 4,
            completion_tokens=len(text) // 4,
            latency_ms=int((time.monotonic() - started) * 1000),
            finish_reason="stop",
        )

    # Providers count the RESERVED answer against the per-minute ceiling, so
    # prompt + max_completion_tokens must fit under it or the request 413s
    # before the model reads a word. Shrink the reservation to whatever room
    # the prompt left rather than sending a request we know will be refused.
    prompt_estimate = sum(len(str(m.get("content", ""))) for m in messages) // 4
    room = cfg.request_token_ceiling - prompt_estimate - cfg.request_token_margin
    max_completion = min(cfg.max_completion_tokens, room)
    if max_completion < cfg.min_completion_tokens:
        raise ModelCallError(
            f"{role}/{model}: prompt is ~{prompt_estimate} tokens, leaving "
            f"{room} of the {cfg.request_token_ceiling} ceiling for an answer "
            f"-- below the {cfg.min_completion_tokens} floor. Shrink "
            f"context_token_budget or raise request_token_ceiling.",
            retryable=False,
        )

    last: Exception | None = None
    for attempt in range(1, MAX_ATTEMPTS + 1):
        try:
            extra: dict[str, Any] = {}
            if "gpt-oss" in model:
                # Only the reasoning models accept this; sending it to others
                # is a 400.
                extra["reasoning_effort"] = cfg.reasoning_effort
            response = _client().chat.completions.create(
                model=model,
                messages=messages,  # type: ignore[arg-type]
                temperature=cfg.temperature,
                seed=cfg.seed,
                max_completion_tokens=max_completion,
                **extra,
            )
        except TRANSIENT as exc:
            last = exc
            if attempt < MAX_ATTEMPTS:
                time.sleep(_sleep_for(attempt))
            continue
        except openai.APIStatusError as exc:
            # A 4xx that is not rate limiting. Re-sending the identical request
            # is pointless, so we do not retry the CALL -- but some of these
            # are the model misbehaving (a spontaneous tool call), not our
            # request being malformed, and a fresh attempt may well succeed.
            # Auth failures never will.
            raise ModelCallError(
                f"{role}/{model}: {exc.status_code} {exc.message}",
                # 413 is "request too large": the identical request will
                # always be too large, so a fresh attempt is wasted budget.
                # Auth failures never recover either.
                retryable=exc.status_code not in (401, 403, 413),
            ) from exc

        choice = response.choices[0]
        text = (choice.message.content or "").strip()
        usage = _usage_from(response, model, role, started)

        if not text and usage.finish_reason == "length":
            # A reasoning model that thought until it hit the cap. Retrying the
            # identical request reproduces it exactly, so failing fast with the
            # real cause beats burning the correctness budget on four attempts
            # that all report "empty response" -- which would land in the
            # results as the model being unable to fix the bug (FR-4), when in
            # fact it never got to answer.
            raise ModelCallError(
                f"{role}/{model}: response truncated at "
                f"{usage.completion_tokens} completion tokens with no content "
                f"-- all of it went to reasoning. Raise max_completion_tokens "
                f"(now {cfg.max_completion_tokens}) or lower reasoning_effort "
                f"(now {cfg.reasoning_effort!r}).",
                retryable=False,
            )
        return text, usage

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
        finish_reason=getattr(response.choices[0], "finish_reason", "") or "",
    )
