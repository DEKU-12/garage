# Prompts

One file per role. **These are versioned artifacts, not code.**

- `config.json` for every run records the SHA-256 of each prompt file used,
  so "did the prompt change between runs" is never a mystery (NFR-1, TAD §6).
- Once experiments start, **never change a prompt and code in the same commit**
  (rules.md §4.1.4). A prompt change invalidates comparisons -- doing it
  mid-experiment is data fraud by accident.

Output contracts live in TAD §3.3. Stubs below; filled in at their build week.
