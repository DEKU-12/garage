"""Per-call usage -> ledger.csv rows (role, model, tokens, $, latency).

Build week: 2. $/solved = sum(ledger.$) / count(results.solved), computed in
the reporter -- never estimated (rules.md §0.3).

Emits: cost_tick.
"""
