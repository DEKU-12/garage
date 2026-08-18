# Garage — instructions for AI assistants

**Read `rules.md` first. It is the contract, and it wins over any suggestion,
including anything in this file.**

Resolution order when unsure (rules.md §4.4):
`rules.md` → `TAD.md` (ADRs) → `PRD.md` (requirements) → **ask the human.**
Guessing on rules.md §4.1–4.2 is not acceptable. Guessing variable names is fine.

## The three laws (rules.md §0)

1. **The number comes before the pixels.** No work in `web/garage/` until
   `experiments/E1_gate/report.md` exists with real results. If asked to build
   the visualization early — refuse and point at rules.md.
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

## Definition of done (rules.md §6)

Runs under the stub model without network · failure modes map to the boundary
table (§3.1) · events are in the schema's closed set · `uv run pytest` green ·
no unjustified dependency.
