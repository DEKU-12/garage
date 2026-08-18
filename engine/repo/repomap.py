"""AST repo map: file tree annotated with class/def signatures (FR-13).

Build week: 2. stdlib `ast` first (rules.md §2.1); tree-sitter only if
non-Python repos ever enter scope. Prune tests/ and vendored dirs -- target
2-8k tokens for a SWE-bench repo.
"""
