# Garage — instructions for AI assistants

**New session? Read `HANDOFF.md` first** — full context in one file.
Then `PLAN.md`, which says what we are building. Then `rules.md`,
which is the contract for how code gets written and wins over any suggestion,
including anything in this file.

`PRD.md`/`TAD.md` describe the benchmark experiment; `BLUEPRINT.md` describes
the product. They conflict, and `PLAN.md` resolves it: **one engine, two front
doors** — a user's GitHub repo, or a SWE-bench task. The benchmark is the
product's evidence layer, not a detour. PRD NG2 and PRD §12 (no user repos) are
superseded; every other rule stands.

Resolution order when unsure:
`PLAN.md` (what) → `rules.md` (how) → `TAD.md` (ADRs) → `PRD.md` →
`BLUEPRINT.md` → **ask the human.**
Guessing on rules.md §4.1–4.2 is not acceptable. Guessing variable names is fine.

## The three laws (rules.md §0)

1. **The number comes before the pixels.** No work in `web/garage/` until
   `experiments/E1_gate/report.md` exists with real results. This now covers
   the product too: an agent ships into repo mode only after it has a benchmark
   number. If asked to build the visualization early — refuse and point here.
2. **Every observable fact flows through `events.jsonl`.** No component reads
   another component's internal state. If the UI needs something, the engine
   emits an event for it.
3. **Never invent a metric.** Numbers come from `report/aggregate.py` over real
   run logs — never typed by hand, never estimated, never "approximately".
   Unfilled numbers are written `XX%`, not plausible-looking placeholders.

## Non-negotiables you will be tempted to break

- `uv` only. Never `pip install`, never poetry, never conda.
- LangGraph only. No CrewAI, AutoGen, or smolagents — the hand-written state
  machine IS the portfolio piece (ADR-2).
- The official `swebench` package grades tests. Never write a custom pytest
  runner for benchmark repos (ADR-3, risk R1).
- Untrusted repo code runs **only** in Docker (NFR-3). Not even once, locally.
- `events.jsonl` is append-only. Never rewrite or "clean up" a log.
- No bare `except:`. `except Exception` exists in exactly ONE place: the
  per-task boundary in the batch runner.
- Everything must run under `--model stub` with no network. A feature that
  can't be exercised without spending API money is built wrong.
- Never change a prompt and code in the same commit once experiments start.

## Layout

`engine/` state machine, agents, repo ops, grading · `server/` FastAPI event
server (reads the filesystem, never imports the engine) · `web/` React + Pixi
· `experiments/` committed configs + result CSVs · `runs/`, `workspaces/`
gitignored. Full map: TAD.md §2. Visual tokens: DESIGN.md.

## At the end of every week

Add a section to `docs/JOURNAL.md` — but only once that week's exit criterion
is genuinely met. Plain English, no jargon, written for someone who does not
know the codebase. Every bug gets four parts: what it looked like, what was
really wrong, how it was fixed, and why it mattered. Include the alternatives
that were rejected and why. Numbers come from real runs (rules.md §0.3).

Bugs that *looked* like model failures but were ours belong there especially —
week 1 had four, and they are the most useful thing in the file.

## Definition of done (rules.md §6)

Runs under the stub model without network · failure modes map to the boundary
table (§3.1) · events are in the schema's closed set · `uv run pytest` green ·
no unjustified dependency.
