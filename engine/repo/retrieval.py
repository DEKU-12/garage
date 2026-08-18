"""Semantic retrieval channel: Chroma index + context-pack assembly (FR-15).

Build week: P1, behind --scout. Must EARN its place via experiment E2 (M4).
Chunk by AST function/class spans, not character windows. Collection keyed by
(repo, base_commit) so the index builds once per task, not per attempt.

Emits: context_pack_ready.
"""
