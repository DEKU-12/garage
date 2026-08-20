"""The human in the loop: nothing is written to your repo unasked.

Build week: 6.

The garage exists to work while you sleep, which is exactly why it must not be
trusted to decide on its own what lands in your repository. Three actions are
gated, in increasing order of consequence:

  1. commit    -- writes a branch in a local checkout
  2. push      -- puts that branch on GitHub, where other people can see it
  3. pull req  -- asks a human to merge it

Four rules hold this together:

* **Silence is never consent.** With no terminal to ask -- cron, CI, a
  scheduler, output piped to a file -- the answer is NO. An agent that treats
  "nobody was there to object" as approval is the exact failure this gate
  exists to prevent.

* **`--yes` is recorded, not hidden.** Running unattended is legitimate, but
  the log must show that no human actually looked, so an approval that came
  from a flag is written down as `assumed_yes` and never as a human decision.

* **An unverified fix is harder to approve than a verified one.** A `pass` has
  a witness test behind it; `unverified` means nothing proved the change does
  anything (engine/eval/repo_grader.py). So a pass takes `y`, and unverified
  takes the whole word `unverified`, typed. A reflex keystroke should not be
  able to ship something nobody can vouch for.

* **You are shown what you are approving** -- the repo, the branch, the base,
  the verdict, the diffstat and the diff itself -- before the question, not
  after.

Decisions are recorded as `gate_verdict` with `gate="human"`. The event schema
is a closed set frozen at v:1 (ADR-10), and a human deciding whether to ship is
a gate in exactly the sense the schema already means -- so it needs no new type,
and it appears on the replay tape like any other verdict.

Emits: nothing directly; the caller emits what this returns.
"""

from __future__ import annotations

import re
import sys
from dataclasses import dataclass

# Approving something you have not read is not approval. Past this many lines
# the diff is truncated with a pointer to the file on disk.
DIFF_PREVIEW_LINES = 60


@dataclass(frozen=True)
class Decision:
    approved: bool
    how: str          # human_yes | human_no | assumed_yes | no_tty | not_asked

    @property
    def by_human(self) -> bool:
        return self.how in ("human_yes", "human_no")


def diffstat(patch: str) -> tuple[int, int, int]:
    """(files, added, removed) -- the shape of the change in one line."""
    files = len(re.findall(r"^diff --git ", patch, re.M))
    added = len(re.findall(r"^\+(?!\+\+)", patch, re.M))
    removed = len(re.findall(r"^-(?!--)", patch, re.M))
    return files, added, removed


def describe(action: str, *, repo: str, branch: str, base: str, verdict: str,
             patch: str, witness: list[str], attempts: int) -> str:
    """Everything needed to decide, before being asked to decide."""
    files, added, removed = diffstat(patch)
    lines = [
        "",
        "=" * 68,
        f"  ABOUT TO {action.upper()}",
        "=" * 68,
        f"  repo      {repo}",
        f"  branch    {branch}   (base: {base})",
        f"  verdict   {verdict}" + ("" if verdict == "pass" else
                                    "   <-- nothing proves this fixes anything"),
        f"  attempts  {attempts}",
        f"  change    {files} file(s), +{added} -{removed}",
    ]
    if witness:
        lines.append(f"  witness   {', '.join(witness)}")
    else:
        lines.append("  witness   NONE -- no test failed before and passed after")
    lines += ["", "-" * 68]
    body = patch.splitlines()
    lines += body[:DIFF_PREVIEW_LINES]
    if len(body) > DIFF_PREVIEW_LINES:
        lines.append(f"... {len(body) - DIFF_PREVIEW_LINES} more lines")
    lines.append("-" * 68)
    return "\n".join(lines)


def ask(action: str, *, verdict: str, assume_yes: bool = False,
        reader=None, out=print, isatty=None) -> Decision:
    """Ask, and treat anything other than a clear yes as a no."""
    if assume_yes:
        out(f"  --yes given: {action} without asking (recorded as assumed_yes)")
        return Decision(True, "assumed_yes")

    interactive = isatty() if isatty else sys.stdin.isatty()
    if not interactive:
        out(f"  no terminal to ask on -- refusing to {action}. "
            "Pass --yes to allow it unattended.")
        return Decision(False, "no_tty")

    strict = verdict != "pass"
    prompt = (f"  Type 'unverified' to {action} anyway, anything else to stop: "
              if strict else f"  {action.capitalize()}? [y/N]: ")
    reader = reader or input
    try:
        answer = reader(prompt).strip()
    except (EOFError, KeyboardInterrupt):
        out("\n  cancelled.")
        return Decision(False, "human_no")

    ok = (answer.lower() == "unverified") if strict else \
         (answer.lower() in ("y", "yes"))
    out("  approved." if ok else "  declined -- nothing was written.")
    return Decision(ok, "human_yes" if ok else "human_no")
