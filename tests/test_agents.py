"""Stub backend, RunConfig, and the single call path -- all offline.

No network, no Docker, no key. If these need any of those, the stub harness is
not doing its job (rules.md §4.3).
"""

from __future__ import annotations

from pathlib import Path

import pytest

from engine.agents.base import call_model
from engine.agents.builder import build_user_message
from engine.agents.stub import MALFORMED_DIFF, PROSE_ONLY, StubBackend, builder_script
from engine.config import STUB_MODEL, RunConfig, prompt_hashes
from engine.errors import ModelCallError


def stub_cfg() -> RunConfig:
    return RunConfig(run_id="t", model_for_role={"builder": STUB_MODEL})


# --- stub backend ---------------------------------------------------------

def test_script_is_consumed_in_order() -> None:
    stub = StubBackend(scripts={"builder": ["one", "two"]})
    assert [stub.next_response("builder") for _ in range(2)] == ["one", "two"]


def test_last_response_repeats_once_exhausted() -> None:
    """A run must not crash just because it out-ran its script."""
    stub = StubBackend(scripts={"builder": ["only"]})
    assert [stub.next_response("builder") for _ in range(3)] == ["only"] * 3


def test_roles_have_independent_cursors() -> None:
    stub = StubBackend(scripts={"builder": ["b1", "b2"], "reviewer": ["r1"]})
    stub.next_response("builder")
    assert stub.next_response("reviewer") == "r1"
    assert stub.next_response("builder") == "b2"
    assert stub.calls_made("builder") == 2


def test_builder_script_puts_failures_before_the_fix() -> None:
    script = builder_script("GOLD", before=[PROSE_ONLY, MALFORMED_DIFF])
    assert script[0] == PROSE_ONLY
    assert script[1] == MALFORMED_DIFF
    assert "GOLD" in script[-1]


# --- call path ------------------------------------------------------------

def test_stub_model_returns_scripted_text_and_estimated_usage() -> None:
    """Stub usage is ESTIMATED from text length, and that is deliberate.

    Real usage is only ever read from the API's own block and never guessed
    (rules.md §0.3) -- see test_real_usage_comes_only_from_the_api. But a stub
    reporting zero tokens can never reach a budget cap, which would leave FR-12
    untestable without spending money -- exactly the shape rules.md §4.3
    forbids. Stub runs are flagged non-results and cannot reach a report, so an
    estimate here costs nothing and buys offline coverage of the cap.
    """
    stub = StubBackend(scripts={"builder": ["a diff"]})
    text, usage = call_model(
        "builder", [{"role": "user", "content": "x" * 40}], stub_cfg(), stub=stub
    )
    assert text == "a diff"
    assert usage.model == STUB_MODEL
    assert usage.prompt_tokens == 10
    assert usage.completion_tokens == len("a diff") // 4


def test_real_usage_comes_only_from_the_api() -> None:
    """Absent fields count as zero -- never inferred from the text."""
    from engine.agents.base import _usage_from

    class _Choice:
        finish_reason = "stop"

    class _Response:
        usage = None
        choices = [_Choice()]

    usage = _usage_from(_Response(), "openai/gpt-oss-20b", "builder", 0.0)
    assert usage.prompt_tokens == 0
    assert usage.completion_tokens == 0


def test_stub_model_without_a_backend_fails_loudly() -> None:
    """Silently returning '' here would look like a model that produced nothing."""
    with pytest.raises(ModelCallError, match="no StubBackend"):
        call_model("builder", [], stub_cfg(), stub=None)


# --- config ---------------------------------------------------------------

def test_unconfigured_roles_default_to_the_stub() -> None:
    """Defaulting to a real model would let a typo start spending money."""
    assert RunConfig(run_id="t").model_for("builder") == STUB_MODEL


def test_is_stub_run_is_false_when_any_role_is_real() -> None:
    assert stub_cfg().is_stub_run  # only builder set, rest default to stub
    mixed = RunConfig(run_id="t", model_for_role={"builder": "llama-3.3-70b"})
    assert not mixed.is_stub_run


def test_config_freezes_with_prompt_hashes(tmp_path: Path) -> None:
    import json

    cfg = RunConfig(run_id="t", prompt_hashes={"builder": "abc123"})
    cfg.freeze_to(tmp_path / "config.json")
    assert json.loads((tmp_path / "config.json").read_text())["prompt_hashes"] == {
        "builder": "abc123"
    }


def test_prompt_hashes_change_when_a_prompt_changes(tmp_path: Path) -> None:
    """This is the whole provenance mechanism -- if it doesn't move, NFR-1 is a lie."""
    (tmp_path / "builder.md").write_text("version one")
    first = prompt_hashes(tmp_path)
    (tmp_path / "builder.md").write_text("version two")
    assert prompt_hashes(tmp_path) != first


def test_prompt_hashes_skip_the_readme(tmp_path: Path) -> None:
    (tmp_path / "README.md").write_text("docs")
    (tmp_path / "builder.md").write_text("prompt")
    assert set(prompt_hashes(tmp_path)) == {"builder"}


# --- builder message ------------------------------------------------------

def test_feedback_is_included_verbatim_on_a_retry() -> None:
    """Git's exact words are the mechanism by which attempt N+1 beats attempt N."""
    stderr = "error: patch failed: django/contrib/auth/validators.py:7"
    msg = build_user_message("the issue", "the source", feedback=stderr)
    assert stderr in msg


def test_first_attempt_has_no_failure_section() -> None:
    assert "previous attempt failed" not in build_user_message("issue", "src")


def test_daily_quota_is_told_apart_from_a_per_minute_rate_limit() -> None:
    """Both arrive as 429s; only one is worth retrying.

    The daily cap appears solely in the error body -- x-ratelimit-* headers
    carry the per-minute limits, so there is nothing to check proactively.
    """
    from engine.agents.base import _is_daily_quota

    daily = Exception(
        "Error code: 429 - {'error': {'message': 'Rate limit reached for model "
        "`openai/gpt-oss-120b` on tokens per day (TPD): Limit 200000, Used 199569'}}"
    )
    per_minute = Exception(
        "Error code: 429 - {'error': {'message': 'Rate limit reached on tokens "
        "per minute (TPM): Limit 8000, Used 7900'}}"
    )
    assert _is_daily_quota(daily)
    assert not _is_daily_quota(per_minute)
