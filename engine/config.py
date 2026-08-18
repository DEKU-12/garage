"""RunConfig: model-per-role, gate flags, retry caps, budgets, seed (TAD §7).

Build week: 1. Pydantic v2 BaseModel; validated at the edge, trusted internally.
Every experiment in PRD §10 is exactly one config file -- no code changes
between arms of an A/B.
"""
