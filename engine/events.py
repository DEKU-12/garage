"""Append-only JSONL event log -- the single source of truth (TAD §4, ADR-1).

Build week: 3 (retrofit into a working engine, deliberately -- the engine must
be provably correct from results.csv alone before observability is layered on).

Schema v:1 closed set:
    run_started, task_started, agent_activated, agent_done, handoff,
    context_pack_ready, patch_produced, patch_apply_error, tests_run,
    gate_verdict, retry, shipped, task_failed, budget_exceeded, cost_tick,
    run_finished

Rules:
- Payloads carry POINTERS, not blobs (ADR-5). Prompts/diffs/logs live in
  attempts/; events reference relative paths.
- `seq` is per-run monotonic -- total order even when timestamps collide.
- Consumers ignore unknown types (forward compatibility).
- This writer FAILS LOUD (rules.md §3.1): if events cannot be written the run
  is worthless, so we crash rather than degrade. The one place death beats
  degradation.
"""
