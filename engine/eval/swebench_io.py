"""Load SWE-bench Lite tasks via the official `swebench` package (FR-1).

Build week: 1. Task loading AND grading come from the official package --
never a custom pytest runner (ADR-3, rules.md §2.2). Per-repo environments
are the known project-killer (R1).
"""
