"""Fresh git worktree per attempt, off a shared bare clone (ADR-4).

Build week: 1. Bare clones cached in workspaces/_bare/<repo>; worktrees are
cheap and guarantee retries never see a dirty tree.
"""
