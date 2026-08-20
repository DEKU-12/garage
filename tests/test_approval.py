"""The human-in-the-loop gate.

The garage runs while you sleep, so the interesting cases are all the ones
where nobody is there to answer. Silence must never read as consent.
"""

from __future__ import annotations

from engine import approval


def _ask(answer, **kw):
    """ask() with the terminal and the reader faked out."""
    return approval.ask(kw.pop("action", "push"),
                        verdict=kw.pop("verdict", "pass"),
                        reader=lambda _prompt: answer,
                        out=lambda *_a, **_k: None,
                        isatty=lambda: kw.pop("tty", True),
                        **kw)


# ------------------------------------------------------- silence is not consent

def test_no_terminal_means_no():
    """cron, CI, output piped to a file: nobody can object, so nothing ships."""
    d = approval.ask("push", verdict="pass", reader=lambda _p: "y",
                     out=lambda *_a: None, isatty=lambda: False)
    assert d.approved is False and d.how == "no_tty"


def test_an_empty_answer_is_a_no():
    assert _ask("").approved is False


def test_a_stray_keystroke_is_a_no():
    assert _ask("maybe later").approved is False


def test_eof_is_a_no():
    def boom(_prompt):
        raise EOFError
    d = approval.ask("push", verdict="pass", reader=boom,
                     out=lambda *_a: None, isatty=lambda: True)
    assert d.approved is False


def test_a_plain_yes_approves_a_verified_fix():
    assert _ask("y").approved is True
    assert _ask("YES").approved is True


# ------------------------------------------- unverified is harder to approve

def test_a_reflex_y_cannot_ship_an_unverified_change():
    """`pass` has a witness test behind it; `unverified` has nothing. A muscle
    -memory keystroke must not be able to ship what nobody can vouch for."""
    assert _ask("y", verdict="unverified").approved is False


def test_unverified_ships_only_when_the_whole_word_is_typed():
    assert _ask("unverified", verdict="unverified").approved is True


# ------------------------------------------------------------- --yes is honest

def test_yes_flag_approves_but_is_recorded_as_not_human():
    d = approval.ask("push", verdict="pass", assume_yes=True, out=lambda *_a: None)
    assert d.approved is True
    assert d.how == "assumed_yes" and d.by_human is False


def test_a_real_answer_is_marked_as_human():
    assert _ask("y").by_human is True
    assert _ask("n").by_human is True


# ------------------------------------------------------------ what you are shown

def test_the_summary_shows_the_change_before_the_question():
    patch = ("diff --git a/x.py b/x.py\n--- a/x.py\n+++ b/x.py\n"
             "@@ -1 +1,2 @@\n-old\n+new\n+extra\n")
    text = approval.describe("push", repo="me/thing", branch="garage/fix-1",
                             base="main", verdict="pass", patch=patch,
                             witness=["tests/test_x.py::test_y"], attempts=2)
    assert "me/thing" in text and "garage/fix-1" in text and "main" in text
    assert "+2 -1" in text                      # the diffstat
    assert "tests/test_x.py::test_y" in text
    assert "+new" in text                       # the diff itself


def test_a_missing_witness_is_stated_not_omitted():
    text = approval.describe("push", repo="r", branch="b", base="main",
                             verdict="unverified", patch="", witness=[], attempts=1)
    assert "NONE" in text
    assert "nothing proves this fixes anything" in text


def test_a_long_diff_is_truncated_rather_than_flooding_the_prompt():
    patch = "diff --git a/x b/x\n" + "\n".join(f"+line{i}" for i in range(500))
    text = approval.describe("push", repo="r", branch="b", base="main",
                             verdict="pass", patch=patch, witness=[], attempts=1)
    assert "more lines" in text
    assert len(text.splitlines()) < 100


def test_diffstat_counts_files_and_lines_not_diff_headers():
    patch = ("diff --git a/a.py b/a.py\n--- a/a.py\n+++ b/a.py\n+one\n-two\n"
             "diff --git a/b.py b/b.py\n--- a/b.py\n+++ b/b.py\n+three\n")
    assert approval.diffstat(patch) == (2, 2, 1)
