# HANDOFF — read this first

Context for a fresh session. Repo: **github.com/DEKU-12/garage**, local at
`~/Desktop/garage`. Written 2026-08-19 after weeks 1–4.

---

## 1. What this is

**A garage that repairs code overnight and can prove its mechanics are worth
running.** One engine, two front doors:

```
   a GitHub URL     ─┐                                      ┌─ your test suite
                     ├─ Scout → Builder → Tester → Reviewer ┤   still passes
   a SWE-bench task ─┘   with gates and bounded retries     └─ the task's
                                     │                         fail_to_pass flips
                          events.jsonl → feed → garage → replay
```

The benchmark half is **not a detour** — it is the only setting where the
correct fix is already known, so it is the only place a claim like *"the
Simplifier is worth +N tasks"* can be earned rather than asserted. Every agent
gets a benchmark number **before** it ships into repo mode.

The user's end goal, in their words: *point it at a repo, sleep, wake up to
pull requests, and watch it happen on screen.*

### Documents, in priority order

| File | Role |
|---|---|
| `PLAN.md` | **what we're building.** Resolves the PRD-vs-blueprint conflict. Wins on scope. |
| `rules.md` | **how code gets written.** Wins on everything else. |
| `TAD.md` | architecture, event schema, 10 ADRs |
| `PRD.md` | requirements (FR-x/NFR-x), experiment plan |
| `BLUEPRINT.md` | the product vision (repair a user's own repo) |
| `docs/JOURNAL.md` | plain-English week-by-week log, written for a non-expert |
| `experiments/E1_gate/report.md` | the committed result |

**Superseded:** PRD NG2 and PRD §12 (no user repos). **Still binding:** PRD NG1,
all three laws in `rules.md` §0, and `rules.md` §1–§6 in full.

---

## 2. Status

| Week | | |
|---|---|---|
| 1 — harness | ✅ | load task, isolated checkout, patch pipeline, Docker grading |
| 2 — the loop + the number | ✅ | **gate is worth +15 points**, measured |
| 3 — event stream | ✅ | events.jsonl → FastAPI → live text feed |
| 4 — the garage | ✅ | Pixi scene driven only by events |
| 5 — replay scrubber | ✅ | tape, fold-to-any-moment, verdict notches, artifact panel |
| 6 — repo front door | 🟡 | 3 verdicts proven vs real Docker; no real-model repair yet |
| 6 — branch / PR creation | 🟡 | branch+commit tested; push/PR never executed |
| experiments E2/E3/E4 | ❌ | caps now fixed and ready |

**162 tests, all green, all offline** (no network, no Docker, no API key).

### The headline result

```
Gate OFF   9/26 (35%)   $0.17/solved   1.1 attempts
Gate ON   13/26 (50%)   $0.46/solved   1.9 attempts
                        → +15 points
```

`claude-sonnet-5`, 26 django tasks from SWE-bench Lite, both arms on the same
tasks. **Published as a LOWER BOUND** — two per-task caps removed the gate arm's
final attempt on 3 tasks, and only the retrying arm can reach a cap.

The mechanism is clearer in the failure breakdown than the headline: tasks where
the builder never produced an appliable diff fell **14 → 7**.

---

## 3. Layout

```
engine/
  cli.py          run-one / run-batch / report
  config.py       RunConfig — every cap and ceiling
  graph.py        LangGraph state machine; gates are EDGE REWIRING, not ifs
  state.py        TaskState / Attempt
  events.py       append-only JSONL log; fails LOUD
  errors.py       ModelCallError, PatchError, QuotaExhausted, WorkspaceError, …
  batch.py        resumable batch, image pruning, resume_conflict guard
  agents/         base (one call path), scout, builder, reviewer, stub
  repo/           workspace (worktrees), patch (extract/validate/apply), repomap, search
  eval/           swebench_io, grader, verify_harness
  accounting/     pricing (real rates, sourced), ledger (one row per call)
  report/         aggregate.py — EVERY reported number comes from here
server/           app.py (FastAPI), tailer.py (150ms poll)
web/feed/         text feed
web/garage/       Pixi scene + vendored pixi.min.js
experiments/E1_gate/   frozen configs, results CSVs, ledgers, report.md
runs/             gitignored; 6 dirs carry INVALID.md — never report from those
```

---

## 4. Current settings and why each number is what it is

```
max_completion_tokens = 32_000   16k truncated hard tasks mid-diff
per_task_usd_cap      = $4.00    $0.50 stole the gate arm's last attempt
per_task_token_cap    = 250_000  must clear 5 builder runs; 150k did not
context_token_budget  = 6_000    the PRD's number, restored off the free tier
REQUEST_TIMEOUT_S     = 900.0    real generations hit 237s against a 120s timeout
SDK max_retries       = 0        our retry loop is the ONLY retry policy
effort                = "high"   Anthropic's knob; there is no token budget
CEILINGS = {groq: 8_000, anthropic: 200_000}
```

**Per-task caps are runaway guards and must never bind in normal operation.**
`--max-usd` on `run-batch` is the actual budget. `tests/test_config.py` asserts
each cap clears a full retry budget — it has caught two undersized guesses.

**Model:** `claude-sonnet-5` primary. Groq (`openai/gpt-oss-20b`, `-120b`) stays
wired for E4; the provider is chosen inside `call_model` from the model id.
Keys in `.env` (gitignored): `ANTHROPIC_API_KEY`, `GROQ_API_KEY`.

---

## 5. Every bug, and the pattern

**Eleven bugs. Nine of them looked like the AI failing when the fault was ours.
Four would have put a wrong number in front of a reader.** This is the single
most important section: the same shapes will recur.

### Silent-zero class — looks fine, is wrong

| # | Bug | Why it mattered |
|---|---|---|
| 1 | `git apply --3way` implies `--index`, so a successful apply leaves changes **staged** and plain `git diff` reports an empty tree | Submitting that empty string scores `empty_patch` — every solved task recorded as a failure. Fix: always `git diff HEAD` (`tree_diff()`) |
| 2 | Groq's **200k tokens-per-day** cap: 43 calls returned 429, never reached the model, each filed as `model_error` | Produced "2/30 both arms, +0% lift". The table rendered; the rows were lies. Only tell: 60 runs in 25 min when one takes 1.7. Fix: `QuotaExhausted` stops the batch and writes **no** result row |
| 3 | **Case-insensitive filesystem** — `--run-id E1` resumed a quarantined `e1`, skipped all 30 tasks, exited 0, printed a Sonnet-titled report full of Groq numbers | Real defect: resume never checked config match. Fix: `resume_conflict()` |
| 4 | **Timeout double-billing** — 120s timeout vs 237s generations; provider bills abandoned work, SDK re-sends underneath our retry loop | **$5.44 charged vs $2.20 recorded.** Found only by comparing the bill to the ledger. Fix: 900s + SDK retries off. Verified exact to the token |

### Experiment-rigging class — biased the arm that retries

| # | Bug |
|---|---|
| 5 | Retry accounting counted *failures seen* as *retries taken* — every gate lost an attempt; the reviewer gave up on its **first** rejection and never once asked for a smaller patch |
| 6 | Reviewer reviewed **failing** patches with the tester gate off, smuggling a retry into the arm defined by having none |
| 7 | `max_completion_tokens=6000` truncated hard tasks mid-diff → looked like the model failing to write a patch |
| 8 | `per_task_usd_cap=$0.50` removed the gate arm's 4th attempt on 3 of 26 tasks |

Only the retrying arm can hit a cap, so **every one of these made the gate look
worse than it is**. That is why E1 is published as a lower bound.

### Quiet-wrong class

| # | Bug |
|---|---|
| 9 | Scout ranked by *count* of matched terms, so `CharField` (hundreds of hits) drowned out `FilePathField` (the class being fixed). The model said in plain words it couldn't see the code. Fix: rarity weighting + definition bonus + spans compete globally |
| 10 | Hunk-header repair silently dropped in a refactor — **all 58 tests still passed**, because every fixture diff was well-formed. Only a real run caught it |
| 11 | A find-and-replace whose search text didn't match: file unchanged, commit message claimed otherwise. An assertion in that same commit caught it |

### Lessons that generalise

1. **Test against known-good AND known-bad inputs.** The gold patch must pass; an
   empty patch must fail. Caught bug #1.
2. **A green suite proves nothing about paths your fixtures never exercise.** Bug #10.
3. **Client-side ledgers are blind to abandoned work.** Reconcile against the
   provider's own usage page. Bug #4.
4. **A backstop that binds has become the limit.** Bugs #7, #8.
5. **Assert config invariants** rather than trusting an edit landed. Bug #11.
6. **Watch the clock** — impossible speed means something is being skipped. Bug #2.

---

## 6. Design decisions worth defending

- **Gates are edge rewiring at graph build time, not `if`s.** With a gate off the
  edge back to the builder is never added — the OFF arm cannot retry even in
  principle. A test inspects the compiled graph.
- **Events carry pointers, not blobs** (ADR-5). `patch_produced` references
  `attempts/2/patch.diff`; the diff is fetched on click.
- **Live and replay are one code path.** `after_seq=0` replays; `after_seq=143`
  resumes a dropped socket. Same request. This is what makes the scrubber cheap.
- **The UI reads the event log and nothing else** (FR-18). No back channel
  exists. That forces the log to be complete and makes the blueprint's promise
  structurally true.
- **The event writer fails LOUD** (`rules.md` §3.1) — the one place the engine
  prefers death to degradation.
- **Fresh git worktree per attempt** (ADR-4). 293MB clone once, 1.2s per worktree.
- **Batch runs both A/B arms per task, then prunes the image** — images are
  ~4.2GB *per task*, so 40 tasks would need ~168GB.
- **`aggregate.py` refuses to report**: stub runs produce no table; `$/solved` is
  withheld when any call was unpriced or nothing solved; comparisons restrict to
  tasks present in every arm.

---

## 7. Commands

```bash
# offline, free, no key
uv run pytest -q
uv run python -m engine.cli run-one --task django__django-11099 --model stub --stub-failures prose --stub-reject --run-id demo

# prove the grader still works (gold passes, empty fails)
uv run python -m engine.eval.verify_harness django__django-11099

# real run — costs money
uv run python -m engine.cli run-batch --tasks 30 --repo django/django \
  --model claude-sonnet-5 --arms on,off --run-id E2 --max-usd 8

uv run python -m engine.cli report runs/E2_on runs/E2_off

# watch it
uv run uvicorn server.app:app --port 8899
#   http://127.0.0.1:8899/         text feed
#   http://127.0.0.1:8899/garage   pixel garage
```

Docker must be running. **Start a run while watching** — a finished run replays
in milliseconds and the garage jumps to its end state. That is the gap the
scrubber closes.

**Costs, measured:** ~$0.09 per task-pair on Sonnet → ~$3 for 30 tasks; budget
**~$8 per experiment**. Groq free tier is unusable for this (200k tokens/day
against ~420k needed).

---

## 8. What to do next

**1. Prove repo mode on one real bug.** Everything is built (`run-repo`), the
front door clones and detects for real, and the verdict logic is covered — but
no end-to-end repair has run, the grader's Docker path has never executed, and
no PR has been opened. Until that happens the honest description is "the
mechanism exists", not "it works".

**2. Repo front door — remaining.** Clone any GitHub URL, detect the test command, build a
work queue. **The hard problem:** an arbitrary repo ships no `fail_to_pass`
list, so grade on *no regressions* + a *witness test* the agent writes that
fails before and passes after. No witness test → report **unverified**, never
as a fix.

**3. Branch + PR creation.** Never touch `main`.

**4. Experiments.** E2 (scout), E3 (ponytail — the publishable one, needs
`--no-reviewer-gate`), E4 (bake-off, needs Groq + Claude both wired — they are).

**5. Extra blueprint agents** — Debugger, Optimizer, Security. Each gets a
benchmark number before shipping into repo mode.

---

## 9. Standing rules for whoever picks this up

- **Never invent a metric.** Numbers come from `aggregate.py` over real logs.
  Unfilled = `XX%`, never a plausible-looking placeholder.
- **Never weaken an experiment to make it pass.** E1's +15 is a lower bound and
  is reported that way — a smaller honest number beats a larger tuned one.
- **Never touch prompts and code in the same commit once experiments start.**
- **Never resume into a run with different settings** (guard exists; do not
  bypass it), and never report from a directory carrying `INVALID.md`.
- **Untrusted repo code runs only in Docker.** A cloned user repo is untrusted,
  and running its test suite is executing it.
- **Everything must run under `--model stub` with no network.**
- **Write the journal entry at each week's exit criterion**, in plain English,
  with every bug as: what it looked like / what was really wrong / how it was
  fixed / why it mattered.
- Resolution order when unsure: `PLAN.md` → `rules.md` → `TAD.md` → `PRD.md` →
  `BLUEPRINT.md` → **ask the human.**
