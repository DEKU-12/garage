"""Canned model responses -- the offline development harness (rules.md §4.3).

Every feature must be exercisable with no API key and no network. If something
can only be run by spending money, it is built wrong. The stub is also what
week 3's frontend develops against: `run-batch --model stub` produces realistic
event streams for free.

A stub script is a list of responses per role, consumed in order, with the last
one repeating once exhausted. Scripting is the point: it is how the retry paths
get exercised deliberately -- give the builder a malformed diff first and a
good one second, and the apply-error -> feedback -> retry loop runs end to end
without a model ever being called.

**A stub run is not a result.** It reports whatever it was scripted to report.
Nothing derived from one may reach a report (rules.md §4.1.1); RunConfig
records `is_stub_run` and the CLI says so loudly.
"""

from __future__ import annotations

from dataclasses import dataclass, field

# Responses that exercise the builder's failure paths on demand.
PROSE_ONLY = "You should change the regex to use \\A and \\Z instead of ^ and $."

MALFORMED_DIFF = """```diff
diff --git a/does/not/exist.py b/does/not/exist.py
--- a/does/not/exist.py
+++ b/does/not/exist.py
@@ -1,2 +1,2 @@
-old
+new
```"""

EMPTY = ""


@dataclass
class StubBackend:
    """Deterministic canned responses, one cursor per role.

    Passed explicitly into `call_model` -- never a module-level global, so two
    runs in one process cannot bleed into each other (rules.md §2.2).
    """

    scripts: dict[str, list[str]] = field(default_factory=dict)
    _cursor: dict[str, int] = field(default_factory=dict, init=False)

    def next_response(self, role: str) -> str:
        """The next scripted response for `role`; the last one repeats."""
        script = self.scripts.get(role) or [""]
        i = self._cursor.get(role, 0)
        self._cursor[role] = i + 1
        return script[min(i, len(script) - 1)]

    def calls_made(self, role: str) -> int:
        return self._cursor.get(role, 0)


def builder_script(gold_patch: str, before: list[str] | None = None) -> list[str]:
    """A builder script that fails however you ask, then produces `gold_patch`.

        builder_script(gold, before=[PROSE_ONLY, MALFORMED_DIFF])

    gives attempt 1 prose, attempt 2 an unappliable diff, attempt 3 the fix --
    which walks the whole correctness-retry loop offline.
    """
    return [*(before or []), f"```diff\n{gold_patch}```"]
