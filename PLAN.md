# PLAN — the merged direction

Two documents in this repo describe different projects. This file says what we
are actually building, and which parts of each still apply. When they conflict,
**this file wins**; `rules.md` remains the contract for *how* code gets written.

| Document | What it describes |
|---|---|
| [BLUEPRINT.md](BLUEPRINT.md) | The **product**: paste a GitHub URL, agents repair the repo, you get a pull request |
| [PRD.md](PRD.md) / [TAD.md](TAD.md) | The **experiment**: SWE-bench Lite tasks, eval gates, a measured success rate |

They are not alternatives. One is the product; the other is the evidence that
the product works.

---

## The end product

**A garage that repairs a repository overnight and can prove its mechanics are
worth running.**

One engine, two front doors:

```
   front door                 the engine (shared)              verdict
   ─────────────────────────────────────────────────────────────────────
   a GitHub URL      ─┐                                  ┌─ your test suite
                      ├─ Scout → Builder → Tester →      ┤   still passes
   a SWE-bench task  ─┘   Reviewer → ship, with gates    └─ the task's
                          and bounded retries                fail_to_pass flips
                                    │
                          events.jsonl → garage UI → replay
```

Everything between the two ends is the same code: the repo map, the fresh
worktree per attempt, the diff pipeline, the state machine, the gates, the
ledger, the event stream, the visualization.

### Why both modes, and why the benchmark is not a detour

BLUEPRINT §9 forbids treating "this should work" as proof, and §18 requires
every claimed improvement to be backed by a real measurement. SWE-bench is the
only setting where the correct fix is already known — so it is the only place a
claim like *"the Simplifier is worth +N solved tasks"* can be earned rather than
asserted.

The benchmark is the product's test suite. BLUEPRINT §13 already puts
`evaluation/` at the top level; this is what goes in it.

Every agent added to the product gets measured on the benchmark **before** it
ships. That is the thing no comparable project has.

---

## What supersedes what

- **PRD NG2 ("arbitrary user repos are a non-goal") and PRD §12
  ("user-supplied repos, GitHub App integration, PR automation" out of scope)
  are SUPERSEDED.** User repos are the product. Those clauses were written when
  this was a benchmark-only project.
- **PRD NG1 (not competing with commercial coding agents on solve rate) still
  holds.** The differentiator is the measurement, not the leaderboard position.
- **rules.md §0.1 ("the number comes before the pixels") still holds**, and now
  covers the product too: an agent ships to repo mode after it has a benchmark
  number, not before.
- **rules.md §0.2 (everything through `events.jsonl`) and §0.3 (never invent a
  metric) hold unchanged**, in both modes.
- Everything in `rules.md` §1–§6 — uv, LangGraph, Docker isolation, the error
  boundary table, append-only events, no bare `except:`, stub-model coverage —
  applies to all new work.

---

## The one genuinely hard problem: what "fixed" means in repo mode

Benchmark mode is unambiguous: the task ships a list of tests that must flip
from failing to passing.

An arbitrary repo ships no such list, so repo mode grades on two things:

1. **No regressions.** The repo's existing test suite must still pass. This is
   enforceable today and catches breakage.
2. **A witness test.** For a bug fix, the agent writes a test that fails on the
   original code and passes on the patched code. That pair *is* a synthesized
   `fail_to_pass` — it manufactures the benchmark's contract for a repo that
   never had one.

Rule 1 alone cannot confirm a bug is fixed; only that nothing broke. Rule 2 is
what makes a repo-mode "solved" mean something. Where the agent cannot produce a
witness test, the change ships as *unverified* and says so — it does not get
counted as a fix.

---

## Build order

Each step is only started once the previous one is genuinely done.

| # | Step | State |
|---|---|---|
| 1 | Harness: load a task, isolated checkout, patch pipeline, Docker grading | **done** (week 1) |
| 2 | Scout, state machine with gates, batch runner, cost ledger, reporting | **done** (week 2 engine) |
| 3 | **E1: the number** — 30 tasks x gate on/off, M1 table | in progress |
| 4 | Repo front door: clone any URL, detect the test command, build the work queue | next |
| 5 | Repo-mode grader: regression run + witness test (the section above) | next |
| 6 | `events.jsonl` + FastAPI server + text feed (TAD §4-5) | week 3 |
| 7 | Pixi garage scene driven only by events (DESIGN.md) | week 4 |
| 8 | **Replay scrubber** — the overnight deliverable: what happened while you slept | after 7 |
| 9 | Branch + pull request creation | after 8 |
| 10 | Blueprint's extra agents — Debugger, Optimizer, Security — each benchmarked before shipping | ongoing |

### Why the replay ranks above more agents

The overnight use case means nobody is watching the live view. The artifact the
user actually consumes is the morning one: a scrubber over the whole night,
verdict stamps as notches on the tape, click a red one to jump to the failure
and see the patch that was tried. TAD FR-26..FR-29 already specify it.

The live view still matters for the first two minutes and for demos — and it is
the same reducer fed from a file instead of a socket, so building one gives the
other nearly free (TAD §5.2).

---

## Provider: why Anthropic, and what it cost

Groq's free tier caps **200,000 tokens per day**. E1 needs ~420,000, so the
experiment could not finish inside a day — three days minimum, with every
debugging iteration costing another one. Time stopped being cheaper than money.

`claude-sonnet-5` is the default. Groq stays wired, selected by model id, so
E4's bake-off can run both without a code change between arms.

Two things this costs, recorded rather than glossed:

- **No seed.** Claude rejects `temperature`, `top_p`, and `seed` outright, so
  NFR-2's seed-pinned reproducibility is gone. Runs are repeated on the same
  task set instead, and `config.json` records the provider behind every result.
- **A dependency.** `anthropic` is justified by the above (rules.md §2.2).

What it buys: no daily wall, and `context_token_budget` back to the PRD's 6000
instead of the 1500 the free tier forced on Scout.

## Non-negotiables for repo mode

- **Never touch `main`.** Every attempt runs on its own branch off a fresh
  worktree; the user's default branch is never a write target.
- **Untrusted code runs only in Docker** (NFR-3, rules.md §4.1.5). A cloned
  repo is untrusted code, and running its test suite is executing it.
- **Budget caps are enforced before the run, not after** (FR-12). An overnight
  run without a ceiling is an unbounded bill.
- **A change with no witness test is reported as unverified**, never as a fix.
- **Every animation corresponds to a real event** (BLUEPRINT §18, rules.md
  §0.2). The visualization is a view of the log, never a script.
