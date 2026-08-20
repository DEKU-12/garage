"""Check the machine can actually do the run, before it costs anything.

Build week: 6.

Repo mode is meant to be run by someone who is not the person who wrote it:
they bring their own Anthropic key and point the garage at their own code. That
person deserves to find out in two seconds that their key has a typo, not three
minutes in, after a 300MB clone and a Docker pull, in the middle of a stack
trace from inside the builder.

Every check answers three things: what is wrong, why it matters, and the exact
command that fixes it. A check that says "FAILED" and nothing else has moved
the problem, not solved it.

ON KEYS: nothing here ever prints, logs, writes or returns a key. The most it
will show is a masked fingerprint (`sk-ant-…a1b2`) so you can tell WHICH key is
loaded when you have several. Keys live in the environment or in .env, and
rules.md §1 keeps them out of code, configs, events and artifacts.

Emits: nothing.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from dataclasses import dataclass

from engine.accounting.pricing import PRICES, is_priced
from engine.agents.base import provider_of

KEY_ENV = {"anthropic": "ANTHROPIC_API_KEY", "groq": "GROQ_API_KEY"}
CONSOLE = {"anthropic": "https://console.anthropic.com/settings/keys",
           "groq": "https://console.groq.com/keys"}


@dataclass(frozen=True)
class Check:
    name: str
    ok: bool
    detail: str
    fix: str = ""          # the exact thing to do about it
    fatal: bool = True     # False = a warning; the run can still go ahead


def mask(key: str) -> str:
    """A key you can recognise but not use. Never the key itself."""
    if not key:
        return "(unset)"
    return f"{key[:7]}…{key[-4:]}" if len(key) > 14 else "…" + key[-3:]


def check_key(model: str) -> Check:
    provider = provider_of(model)
    var = KEY_ENV[provider]
    key = os.environ.get(var, "").strip()
    if not key:
        return Check(
            f"{var} present", False, "not set",
            fix=(f"Get a key from {CONSOLE[provider]}, then either:\n"
                 f"      cp .env.example .env    and put it in that file\n"
                 f"      (or)  export {var}=your-key-here\n"
                 f"    Or work offline for free with --model stub."))
    if provider == "anthropic" and not key.startswith("sk-ant-"):
        return Check(
            f"{var} present", False, f"loaded, but {mask(key)} is not shaped "
            "like an Anthropic key (they start sk-ant-)",
            fix=f"Check you have not pasted a key for a different service into {var}.")
    return Check(f"{var} present", True, mask(key))


def check_key_works(model: str, timeout_s: float = 30.0) -> Check:
    """Spend one token to find out, rather than three minutes of clone first.

    A live call is the only thing that distinguishes a valid key from a
    revoked one, a typo, or an account with no credit -- and each of those
    needs a different fix.
    """
    provider = provider_of(model)
    var = KEY_ENV[provider]
    try:
        if provider == "anthropic":
            import anthropic
            client = anthropic.Anthropic(api_key=os.environ.get(var, ""),
                                         timeout=timeout_s, max_retries=0)
            client.messages.create(model=model, max_tokens=1,
                                   messages=[{"role": "user", "content": "hi"}])
        else:
            from openai import OpenAI
            from engine.agents.base import GROQ_BASE_URL
            OpenAI(base_url=GROQ_BASE_URL, api_key=os.environ.get(var, ""),
                   timeout=timeout_s, max_retries=0).chat.completions.create(
                model=model, max_tokens=1,
                messages=[{"role": "user", "content": "hi"}])
        return Check(f"{var} works", True, f"{model} answered")
    except Exception as exc:                       # noqa: BLE001 -- reported, not swallowed
        text = str(exc)
        low = text.lower()
        if "authentication" in low or "401" in low or "invalid x-api-key" in low:
            fix = (f"The key loaded ({mask(os.environ.get(var, ''))}) was rejected. "
                   f"Make a fresh one at {CONSOLE[provider]}.")
        elif "credit" in low or "billing" in low or "quota" in low or "429" in low:
            fix = ("The key is valid but the account cannot pay for the call. "
                   "Check credit/limits on the provider's console.")
        elif "not_found" in low or "404" in low:
            fix = (f"The key works but {model!r} is not available to it. "
                   "Check the model id, or that your account has access.")
        else:
            fix = "Looks like a network or provider problem rather than your key."
        return Check(f"{var} works", False, text[:160], fix=fix)


def check_docker() -> Check:
    if not shutil.which("docker"):
        return Check("docker", False, "not installed",
                     fix="Install Docker Desktop. Repo mode runs a cloned "
                         "repo's test suite, and untrusted code only ever runs "
                         "in a container.")
    proc = subprocess.run(["docker", "info"], capture_output=True, text=True,
                          timeout=30, check=False)
    if proc.returncode != 0:
        return Check("docker", False, "installed but not running",
                     fix="Start Docker Desktop and wait for it to say Running.")
    return Check("docker", True, "running")


def check_git() -> Check:
    return (Check("git", True, "found") if shutil.which("git")
            else Check("git", False, "not installed", fix="Install git."))


def check_gh(needed: bool) -> Check:
    """Only fatal when a pull request was actually asked for."""
    if not shutil.which("gh"):
        return Check("gh (pull requests)", False, "not installed",
                     fix="Install the GitHub CLI (`brew install gh`) and run "
                         "`gh auth login`. Only needed with --pr.",
                     fatal=needed)
    proc = subprocess.run(["gh", "auth", "status"], capture_output=True,
                          text=True, timeout=30, check=False)
    if proc.returncode != 0:
        return Check("gh (pull requests)", False, "installed but not logged in",
                     fix="Run `gh auth login`. Only needed with --pr.",
                     fatal=needed)
    return Check("gh (pull requests)", True, "authenticated")


def check_price(model: str) -> Check:
    """An unpriced model runs fine and reports no cost, which is worse than
    refusing: aggregate.py withholds $/solved rather than print a wrong one."""
    if is_priced(model):
        p = PRICES[model]
        return Check("pricing known", True,
                     f"{model}: ${p.input_per_m}/M in, ${p.output_per_m}/M out")
    return Check("pricing known", False, f"no published rate for {model!r}",
                 fix="Add it to engine/accounting/pricing.py with a source, or "
                     "expect cost-per-solve to be withheld from reports.",
                 fatal=False)


def preflight(model: str, *, want_pr: bool = False, need_docker: bool = True,
              live: bool = True) -> list[Check]:
    """Everything worth knowing before a run that costs money."""
    checks = [check_git(), check_key(model), check_price(model)]
    if need_docker:
        checks.append(check_docker())
    if want_pr or shutil.which("gh"):
        checks.append(check_gh(want_pr))
    # Only worth a live call if the key looked sane locally.
    if live and checks[1].ok:
        checks.append(check_key_works(model))
    return checks


def render(checks: list[Check]) -> str:
    lines = []
    for c in checks:
        mark = "ok  " if c.ok else ("FAIL" if c.fatal else "warn")
        lines.append(f"  [{mark}] {c.name}: {c.detail}")
        if not c.ok and c.fix:
            for i, line in enumerate(c.fix.splitlines()):
                lines.append(("    -> " if i == 0 else "       ") + line.strip())
    return "\n".join(lines)


def blocking(checks: list[Check]) -> list[Check]:
    return [c for c in checks if not c.ok and c.fatal]
