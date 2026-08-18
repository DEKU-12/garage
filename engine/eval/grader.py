"""Thin wrapper over the official SWE-bench Docker evaluation harness (FR-5).

Build week: 1. This wrapper working for one task IS the week-1 exit criterion.

    grade(task_id, patch) -> {"verdict": "pass"|"fail", "log_tail": str}

Untrusted repo code executes ONLY inside Docker (NFR-3, rules.md §4.1.5) --
not even "just this once to debug locally".

Per-task timeout (default 20 min). A timeout or infra failure is task status
`crashed`, NOT `failed_tests` -- never blame the model for infrastructure.

Emits: tests_run, gate_verdict.
"""
