"""The single model call path: call_model(role, messages, cfg) -> (text, Usage).

Build week: 1. No agent calls the Groq API directly (rules.md §2.1).
Temperature and seed fixed per RunConfig (NFR-2).

Failure handling (rules.md §3.1): retry with exponential backoff + jitter,
max 3; timeout 120 s; on final failure raise ModelCallError, caught at the
per-task boundary.

Must support `--model stub` (canned responses, no network) so every feature is
exercisable without spending API money (rules.md §4.3).
"""
