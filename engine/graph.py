"""LangGraph wiring: nodes and edges only, no business logic (TAD §3.2).

Build week: 2. Termination proof: builder runs at most 1 + 3 + 1 = 5 times.

ADR-2: hand-written graph, not CrewAI/AutoGen -- the explicit state machine
is the portfolio piece.

Gate-off modes (FR-10) are implemented as edge rewiring at graph build time,
NOT `if` statements inside nodes: the OFF configuration must be structurally
incapable of invoking the gate, which is what makes the A/B clean.
"""
