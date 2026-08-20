"""Preflight: fail in two seconds, not three minutes (repo front door).

The point of these checks is that somebody who is NOT the author can bring
their own key and find out immediately if it is wrong. So the tests care about
two things: that a bad key is caught, and that a key is never leaked.
"""

from __future__ import annotations

import pytest

from engine import preflight as pf


# ------------------------------------------------------------- never leak

def test_mask_never_returns_the_key():
    key = "sk-ant-api03-SECRETSECRETSECRET-a1b2"
    out = pf.mask(key)
    assert key not in out
    assert "SECRET" not in out
    assert out.startswith("sk-ant-") and out.endswith("a1b2")


def test_mask_of_a_short_string_still_hides_it():
    assert "abcdefg" not in pf.mask("abcdefg")


def test_an_unset_key_masks_to_a_marker_not_an_empty_string():
    assert pf.mask("") == "(unset)"


def test_a_failing_check_never_carries_the_key_in_its_text(monkeypatch):
    """The fix text quotes a masked fingerprint. If it ever quoted the real
    key, the key would land in terminal scrollback and CI logs."""
    key = "sk-wrong-TOTALLYSECRETVALUE"
    monkeypatch.setenv("ANTHROPIC_API_KEY", key)
    c = pf.check_key("claude-sonnet-5")
    assert not c.ok
    assert key not in (c.detail + c.fix)
    assert "TOTALLYSECRETVALUE" not in (c.detail + c.fix)


# ------------------------------------------------------------- the checks

def test_a_missing_key_is_fatal_and_says_how_to_get_one(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    c = pf.check_key("claude-sonnet-5")
    assert not c.ok and c.fatal
    assert "console.anthropic.com" in c.fix
    assert "--model stub" in c.fix        # the free way out is always offered


def test_a_key_for_the_wrong_service_is_caught_before_the_run(monkeypatch):
    """Pasting a Groq key into ANTHROPIC_API_KEY is a real mistake, and one a
    live call would only reveal after the clone."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "gsk_abcdefghijklmnop")
    c = pf.check_key("claude-sonnet-5")
    assert not c.ok and "sk-ant-" in c.fix + c.detail


def test_a_plausible_key_passes_the_local_check(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-api03-abcdefghijklmnop")
    assert pf.check_key("claude-sonnet-5").ok


def test_the_groq_path_looks_at_the_groq_variable(monkeypatch):
    monkeypatch.delenv("GROQ_API_KEY", raising=False)
    c = pf.check_key("openai/gpt-oss-20b")
    assert not c.ok and "GROQ_API_KEY" in c.name


def test_an_unpriced_model_warns_but_does_not_block():
    """It runs fine; it just cannot be reported on, and aggregate.py withholds
    $/solved rather than print a wrong one."""
    c = pf.check_price("some-model-nobody-priced")
    assert not c.ok and not c.fatal


def test_a_priced_model_shows_its_rate():
    c = pf.check_price("claude-sonnet-5")
    assert c.ok and "/M in" in c.detail


def test_gh_is_only_fatal_when_a_pull_request_was_asked_for():
    assert pf.check_gh(needed=False).fatal is False


def test_blocking_ignores_warnings():
    checks = [
        pf.Check("a", False, "broken", fatal=True),
        pf.Check("b", False, "meh", fatal=False),
        pf.Check("c", True, "fine"),
    ]
    assert [c.name for c in pf.blocking(checks)] == ["a"]


def test_render_shows_the_fix_under_a_failure():
    out = pf.render([pf.Check("thing", False, "nope", fix="do the thing")])
    assert "FAIL" in out and "do the thing" in out


def test_preflight_skips_the_live_call_when_the_key_is_already_wrong(monkeypatch):
    """No point spending a token to confirm a key that is not even set."""
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    names = [c.name for c in pf.preflight("claude-sonnet-5", need_docker=False)]
    assert not any("works" in n for n in names)


def test_an_exported_key_beats_the_dotenv_file(tmp_path, monkeypatch):
    """`KEY=... garage run-repo` must actually use KEY.

    With override=True a shell variable was silently ignored in favour of
    .env -- so you could believe you were testing one key while spending on
    another. Found the hard way: a deliberately-invalid key was overridden by
    the real one and the run went ahead and cost money.
    """
    from dotenv import load_dotenv

    env_file = tmp_path / ".env"
    env_file.write_text("ANTHROPIC_API_KEY=sk-ant-from-the-file\n")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-from-the-shell")
    load_dotenv(env_file, override=False)

    import os
    assert os.environ["ANTHROPIC_API_KEY"] == "sk-ant-from-the-shell"
