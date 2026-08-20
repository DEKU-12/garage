# Does a one-line bug deserve 26,000 tokens of thinking?

**No — measured, not assumed.** On the same nine mutants, `effort=low` with an
8k completion cap reached an identical verdict to the default `effort=high`
with 32k, for a fifth of the tokens and a quarter of the wall clock.

## The numbers

| | high (default) | low |
|---|---|---|
| tests green | 7/9 | 7/9 |
| **line restored** | **6/9** | **6/9** |
| same verdict per mutant | — | **9/9** |
| total attempts | 13 | 16 |
| model calls | 20 | 24 |
| output tokens | 109,898 | 21,527 |
| cost | $1.25 | $0.41 |
| wall clock | 50 min | 12 min |

Runs: `M1` (high), `M2_low` (low). Set: `experiments/mutants/nl2sql.json`,
9 mutants of DEKU-12/NL2SQL @ 4b563598, `claude-sonnet-5`.

## Why this was worth measuring

`max_completion_tokens=32_000` and `effort="high"` were chosen for SWE-bench:
16k *"truncated hard tasks mid-diff"*. Nobody had asked what they cost on an
easy task. The answer was 26,507 output tokens and 4.4 minutes on a single
flipped `or` — and a 50-mutant experiment at that rate is five hours, which
was about to make E2 and E3 painful enough to keep postponing.

## The mechanism, which is the interesting part

Low effort took **more attempts** (16 vs 13) and got to the same place. It
thinks less, is wrong more often, and the retry loop absorbs it.

That is E1's thesis in a second setting: cheap attempts behind a gate can
substitute for expensive deliberation. E1 measured that retrying beats not
retrying; this suggests retrying also beats thinking harder, at least where
the failure is cheap to detect.

## What this does NOT show

- **n=9**, three files of one repo. Suggestive, not conclusive.
- **One-line mutations with the failing tests named.** The default was tuned
  for genuinely hard tasks and may well earn its keep there. Nothing here
  contradicts that; it was never tested on this kind of task before.
- The identical 6/9 includes the identical *failure* to repair
  `prompt_builder-L118` — both efforts produced the same workaround, and the
  reviewer accepted it both times. Less thinking did not cause that; more
  thinking did not prevent it.

## What changed because of it

`run-mutants` takes `--effort` and `--max-tokens`. E2 and E3 will run at low
effort on this task class: ~1 hour and ~$2 for 50 mutants instead of ~5 hours
and ~$6. The default is untouched for benchmark and repo runs, where it has
not been measured.
