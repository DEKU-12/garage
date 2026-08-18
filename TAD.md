# Technical Architecture Document
## Multi-Agent Coding System with Eval Gates and Visual Tracing ("The Garage")

| | |
|---|---|
| **Status** | Draft v1.0 |
| **Date** | August 2026 |
| **Companion doc** | PRD v1.0 (requirements referenced as FR-x / NFR-x) |
| **Audience** | The author (build reference) + interviewers (system walkthrough) |

---

## 1. Architecture Overview

### 1.1 The one design rule

**Everything observable flows through one append-only event log.** The state machine emits events; the file system stores them; the server broadcasts them; the UI renders them. No component reads another component's internal state. This single decision buys: live view and replay from the same code path (FR-18, FR-26), crash-safe resumability (NFR-4), and metrics that are recomputable from raw logs (FR-31, NFR-1).

### 1.2 System context (C4 level 1)

```
                        ┌─────────────────────┐
   author / demo  ───►  │   Browser (React)    │
                        └─────────┬───────────┘
                                  │ WS + REST
                        ┌─────────┴───────────┐
                        │  Event Server        │
                        │  (FastAPI)           │
                        └─────────┬───────────┘
                                  │ tails / reads
                        ┌─────────┴───────────┐
                        │  Run artifacts       │  runs/<run_id>/…
                        │  (filesystem)        │  events.jsonl, attempts/
                        └─────────▲───────────┘
                                  │ writes
                        ┌─────────┴───────────┐        ┌──────────────┐
                        │  Engine              │ ─────► │  Groq API    │
                        │  (LangGraph, Python) │        └──────────────┘
                        │                      │        ┌──────────────┐
                        │                      │ ─────► │ SWE-bench    │
                        └─────────┬───────────┘        │ Docker eval  │
                                  │ git / rg / AST      └──────────────┘
                        ┌─────────┴───────────┐
                        │  Task workspaces     │  workspaces/<task>/<attempt>/
                        └──────────────────────┘
```

Three processes, loosely coupled through the filesystem:

| Process | Language | Owns | Talks to |
|---|---|---|---|
| **Engine** | Python 3.12 | State machine, agents, repo ops, test grading, event emission | Groq API, Docker, filesystem |
| **Event Server** | Python (FastAPI) | Live WS broadcast, run-log REST, static frontend | Filesystem (read-only) |
| **Frontend** | React + Pixi.js | Text feed, garage scene, replay | Event Server only |

The Engine never imports the server; the server never imports the engine. They meet at `runs/<run_id>/events.jsonl`. The engine can run headless (weeks 1–2 have no server at all).

---

## 2. Repository Layout

```
garage/
  engine/
    __init__.py
    cli.py                # entrypoints: run-one, run-batch, report
    config.py             # RunConfig: model-per-role, flags, caps, seeds
    graph.py              # LangGraph wiring (nodes + edges only)
    state.py              # TaskState TypedDict + reducers
    events.py             # emit(), event schema, JSONL writer
    agents/
      base.py             # call_model(role, messages) -> (text, usage)
      orchestrator.py
      scout.py
      builder.py
      tester.py
      reviewer.py
      scribe.py
    prompts/              # one .md per role, version-controlled (NFR-1)
      orchestrator.md
      scout.md
      builder.md
      tester.md
      reviewer.md         # the ponytail ladder lives here
      scribe.md
    repo/
      workspace.py        # clone/worktree per attempt, cleanup
      repomap.py          # AST -> file tree + signatures
      search.py           # ripgrep wrapper
      retrieval.py        # Chroma index (P1), context-pack assembly
      patch.py            # diff validation + apply (git apply)
    eval/
      swebench_io.py      # load tasks from the `swebench` package
      grader.py           # submit patch to Docker harness, parse verdict
    accounting/
      pricing.py          # per-model $ table
      ledger.py           # per-attempt usage -> cost rows
    report/
      aggregate.py        # run logs -> M1..M6 tables (FR-31)
  server/
    app.py                # FastAPI: /ws/live, /api/runs, /api/runs/{id}/events
    tailer.py             # follows events.jsonl, fans out to WS clients
  web/
    src/
      feed/               # week 3: plain text feed
      garage/             # week 4: Pixi scene, sprites, stations
      replay/             # later: scrubber
      api.ts              # WS + REST client, shared event types
  runs/                   # gitignored; one dir per run (see §6)
  workspaces/             # gitignored; repo checkouts
  experiments/            # committed: configs + frozen result CSVs per experiment
  tests/
```

---

## 3. The Engine

### 3.1 State (LangGraph)

One graph invocation = one SWE-bench task. State is a single TypedDict; nodes return partial updates.

```python
class Attempt(TypedDict):
    n: int                      # attempt number, 1-based
    patch: str                  # unified diff
    patch_applied: bool
    test_verdict: str | None    # "pass" | "fail" | None (not run)
    test_output: str | None     # truncated failing output for feedback
    review_verdict: str | None  # "accept" | "reject" | None
    review_reason: str | None   # ladder rung + paragraph
    usage: dict                 # prompt/completion tokens per role
    wall_ms: int

class TaskState(TypedDict):
    task_id: str
    issue: str
    repo: str
    base_commit: str
    fail_to_pass: list[str]     # tests that must flip
    context_pack: list[dict]    # [{file, start, end, why}]
    attempts: list[Attempt]
    correctness_retries: int    # 0..3
    simplicity_retries: int     # 0..1
    status: str                 # running | shipped | failed_tests |
                                # failed_review | patch_apply_error |
                                # budget_exceeded | crashed
    spend_usd: float
```

### 3.2 Graph topology

```
            ┌────────────┐
   START ──►│ orchestrate │  parse issue, init state
            └─────┬──────┘
                  ▼
            ┌────────────┐
            │   scout    │  (skipped if --no-scout: context = issue only)
            └─────┬──────┘
                  ▼
      ┌─────►┌────────────┐
      │      │  builder   │  emits patch
      │      └─────┬──────┘
      │            ▼
      │      ┌────────────┐   apply fail ──► retry (counts toward cap)
      │      │   tester   │
      │      └─────┬──────┘
      │   fail &   │ pass
      │   retries  ▼
      │   left ┌────────────┐   (skipped if --no-reviewer-gate → ship)
      └────────│  reviewer  │
      ▲        └─────┬──────┘
      │  reject &    │ accept
      │  retry left  ▼
      └──────── ┌────────────┐
                │   ship     │  scribe summary, final metrics event
                └─────┬──────┘
                      ▼
                     END
```

Conditional edges (the only routing logic, all in `graph.py`):

```python
def after_tester(state) -> str:
    a = state["attempts"][-1]
    if not a["patch_applied"] or a["test_verdict"] == "fail":
        if state["correctness_retries"] < cfg.max_correctness_retries:  # 3
            return "builder"
        return "fail_tests"
    return "reviewer" if cfg.reviewer_gate else "ship"

def after_reviewer(state) -> str:
    a = state["attempts"][-1]
    if a["review_verdict"] == "reject":
        if state["simplicity_retries"] < cfg.max_simplicity_retries:    # 1
            return "builder"
        return "ship_anyway"   # design choice: simplicity never blocks a
                               # correct patch at cap — record verdict, ship
    return "ship"
```

**Termination proof:** builder runs at most `1 + 3 + 1 = 5` times per task. Budget caps (FR-12) checked at every node entry can end it earlier with `budget_exceeded`.

**Gate-off modes (FR-10)** are implemented as edge rewiring at graph build time, not `if`s inside nodes — the OFF configuration literally cannot invoke the gate, which makes the A/B clean.

### 3.3 Agents

All agents share one call path:

```python
def call_model(role: str, messages: list, cfg: RunConfig) -> tuple[str, Usage]:
    model = cfg.model_for_role[role]      # bake-off (E4) = a config dict
    # temperature and seed fixed per RunConfig (NFR-2)
```

| Agent | Input | Output contract | Notes |
|---|---|---|---|
| Orchestrator | raw issue | normalized problem statement + suspected area keywords | Cheapest call; can be a template in v1 |
| Scout | issue + repo map; may call `search()` up to K times | context pack: `[{file, start, end, why}]` under `cfg.context_token_budget` | Tool loop bounded at K=6 calls |
| Builder | issue + context pack + prior failure feedback | **unified diff only**, fenced; nothing else | Strictest output contract in the system (see §3.4) |
| Tester | (patch, task) — mostly *not* an LLM | verdict from Docker harness | LLM used only to summarize failing output into feedback ≤ N tokens |
| Reviewer | the diff (+ optional ±20 lines context — open question Q2) | `ACCEPT` or `REJECT / rung <1-6> / <reason ¶>` | Prompt = ponytail ladder; parsed with a strict regex, malformed → treated as ACCEPT and logged |
| Scribe | final state | markdown run summary | No authority; behind a flag; cut first |

### 3.4 The patch pipeline (highest-risk component, R3)

Model-generated diffs fail constantly in practice. Defense in depth:

1. **Prompt contract:** builder prompt shows one worked example of exact `diff --git` format; forbids prose.
2. **Extraction:** take the last fenced block; strip anything before `diff --git`/`---`.
3. **Validation:** paths must exist in the checkout; hunk headers must parse; reject early with a specific reason.
4. **Apply:** `git apply --3way` in the attempt's workspace; capture stderr.
5. **On failure:** emit `patch_apply_error` event, feed git's error back to the builder verbatim. Apply failures count toward the correctness cap (prevents infinite malformed-diff loops) but are tracked as their own failure type in reporting (FR-4) — "model can't format diffs" and "model can't fix bugs" are different findings.

Each attempt gets a **fresh `git worktree`** off a shared bare clone (one clone per repo, cached in `workspaces/_bare/`; worktrees are cheap). Guarantees retries never see a dirty tree.

### 3.5 Test grading

Never run repo tests directly on the host (NFR-3). The grader is a thin wrapper around the official SWE-bench Docker evaluation:

```
grader.grade(task_id, patch) ->
  writes predictions JSONL → invokes swebench harness (subprocess, Docker)
  → parses report → {"verdict": "pass"|"fail", "log_tail": str}
```

Docker images per repo are pulled lazily and cached; a warm-cache batch run only pays container start time. Week-1 exit criterion is this wrapper working for one task.

### 3.6 Retrieval (Scout internals)

Three channels, cheapest first, all feeding one context-pack assembler:

1. **Repo map** (always): `ast.parse` every `.py` file → file tree annotated with class/def signatures + docstring first lines. Typically 2–8k tokens for SWE-bench repos after pruning tests/vendored dirs.
2. **Lexical** (always): `rg --json` wrapper; Scout issues keyword queries derived from the issue.
3. **Semantic** (P1, flag-gated for E2): chunk by function/class boundaries (AST spans, not character windows), embed, Chroma persistent collection keyed by `(repo, base_commit)` so the index builds once per task, not per attempt.

Assembler dedupes overlapping spans, ranks by (scout-stated relevance, channel agreement), and cuts at the token budget. The pack is stored in state and in the attempt artifacts — so "what did the builder actually see" is always answerable in replay (FR-28).

---

## 4. Event System

### 4.1 Schema (frozen before week 4 — Q5 resolved: freeze at week 3 exit)

One JSON object per line, append-only, flushed per event:

```json
{
  "v": 1,
  "ts": "2026-08-18T14:03:22.481Z",
  "seq": 143,
  "run_id": "r_2026-08-18_a3f2",
  "task_id": "django__django-11099",
  "agent": "tester",
  "type": "gate_verdict",
  "payload": {"gate": "tests", "verdict": "fail", "attempt": 2,
               "artifact": "attempts/2/test_output.txt"}
}
```

Rules:
- `seq` is a per-run monotonic integer → total order even if timestamps collide; the scrubber scrubs over `seq`, displays `ts`.
- **Payloads carry pointers, not blobs.** Prompts, diffs, test logs live in `attempts/`; events reference relative paths. Keeps the stream small enough to tail and the UI fast; FR-28's hover fetches the artifact lazily over REST.
- Event types (closed set, `v:1`): `run_started`, `task_started`, `agent_activated`, `agent_done`, `handoff`, `context_pack_ready`, `patch_produced`, `patch_apply_error`, `tests_run`, `gate_verdict`, `retry`, `shipped`, `task_failed`, `budget_exceeded`, `cost_tick`, `run_finished`.
- Unknown types must be ignored by consumers (forward compatibility).

### 4.2 Why JSONL-on-disk instead of a queue/DB

Considered: SQLite table, Redis pub/sub, in-process queue. Chosen: flat JSONL because (a) replay = the live file, zero translation; (b) crash-safe by construction — a killed run's log is valid up to its last line; (c) `grep`/`jq`-able during debugging; (d) the aggregate reporter (FR-31) is a pure function over files, trivially rerunnable. Scale is bounded: ~200–400 events/task × 50 tasks ≈ 20k lines/run. A database earns its place only if this ever becomes multi-user — which is out of scope (NG3).

---

## 5. Event Server & Frontend

### 5.1 Server (FastAPI)

| Endpoint | Purpose |
|---|---|
| `GET /api/runs` | list run dirs + status summary |
| `GET /api/runs/{id}/events?after_seq=` | full or incremental log (replay + WS catch-up) |
| `GET /api/runs/{id}/artifacts/{path}` | lazy artifact fetch (prompts, diffs, logs) — path-sanitized to the run dir |
| `WS /ws/live/{run_id}` | pushes each new event as it lands |

Live tailing: a `tailer` task per active run polls `events.jsonl` for appended lines (150 ms interval — comfortably inside NFR-6's 250 ms) and fans out to connected sockets. On WS connect the client sends its last `seq`; the server replays the gap from disk then switches to live. **Reconnect and cold-start are therefore the same code path.**

### 5.2 Frontend state model

One reducer, two sources, identical events:

```
(WS live) ──┐
            ├──► event reducer ──► SceneState ──► Pixi renderer
(REST log)──┘                            └──────► text feed / HUD
```

`SceneState` = `{agents: {name: {station, activity}}, currentTask, attempt, verdicts, spend}` — derived 100% from events (FR-18). The garage scene is a *pure view* of `SceneState`:

- `agent_activated` → walk avatar from couch to its station, play "working" loop
- `handoff` → paper sprite tweens between stations
- `gate_verdict` → green/red stamp animation at the bench
- `agent_done` → walk back to couch

Replay = feed the same reducer from the REST log with a clock multiplier; the scrubber sets a target `seq`, and the reducer — which is a fold — recomputes state by replaying `events[0..seq]` (20k events folds in ms; no snapshotting needed at this scale).

### 5.3 Sprites

Kenney.nl CC0 sheets; 6 characters differentiated by palette swap; stations are static props on a hand-placed tilemap (no pathfinding grid needed — precomputed waypoint paths couch↔station are enough for v1, an honest simplification vs. Munder Difflin's pathfinding).

---

## 6. Storage Layout

```
runs/r_2026-08-18_a3f2/
  config.json               # frozen RunConfig: models, flags, caps, seed, prompt hashes
  events.jsonl              # the log (source of truth)
  tasks/
    django__django-11099/
      attempts/
        1/ prompt_builder.md  response.md  patch.diff  test_output.txt
        2/ …
      review/ verdicts.jsonl
      summary.md            # scribe
  ledger.csv                # one row per model call: role, tokens, $, ms
  results.csv               # one row per task: solved, attempts, $, failure_type
experiments/E1_gate/
  config_on.json  config_off.json  results_on.csv  results_off.csv  report.md
```

`config.json` includes **SHA-256 hashes of every prompt file** used — an experiment's provenance is checkable, and "did the prompt change between runs" is never a mystery (NFR-1).

Batch resumability (FR-11): the runner scans `results.csv` at start and skips completed `task_id`s; a task with events but no result row is re-run from scratch (its old attempt dir is archived, not overwritten).

---

## 7. Configuration

```python
class RunConfig(BaseModel):
    run_id: str
    model_for_role: dict[str, str]      # E4 bake-off lives here
    tester_gate: bool = True            # FR-10
    reviewer_gate: bool = True
    scout: bool = True
    max_correctness_retries: int = 3
    max_simplicity_retries: int = 1
    context_token_budget: int = 6000
    scout_max_tool_calls: int = 6
    per_task_usd_cap: float = 0.50      # FR-12
    per_run_usd_cap: float = 15.00
    temperature: float = 0.2
    seed: int | None = 7
    task_ids: list[str]
```

CLI: `uv run garage run-batch --config experiments/E1_gate/config_on.json`. Every experiment in the PRD's §10 is exactly one config file — no code changes between arms of an A/B.

Secrets: `.env` at project root, `load_dotenv(override=True)` (author's convention); keys never appear in events, artifacts, or configs.

---

## 8. Cross-Cutting Concerns

### 8.1 Cost accounting (FR-32, M2)
Every `call_model` return includes the API's usage block → one `ledger.csv` row (role, model, prompt/completion tokens, $ from `pricing.py`, latency) + a `cost_tick` event so the HUD's spend counter is live. **$/solved = sum(ledger.$) / count(results.solved)** — computed in the reporter, never estimated.

### 8.2 Failure taxonomy
Every non-shipped task ends in exactly one of: `failed_tests` (cap hit), `failed_review` (only if ship-anyway is disabled), `patch_apply_error` (never produced an appliable diff), `budget_exceeded`, `crashed` (unhandled exception — engine wraps each task in a try/except that emits `task_failed{reason:"crashed", trace}` and continues the batch, NFR-4). The M1 table breaks failures down by type; this is where the interesting engineering stories live.

### 8.3 Concurrency
v1 is **sequential across tasks** (Q3 resolved for v1): Docker grading is the bottleneck and serializing it avoids image-cache races and keeps the live visualization coherent (one task on the floor at a time). The design leaves room: state is per-task, events carry `task_id`, and the only shared mutables are the ledger file (append with a lock) and the Docker cache.

### 8.4 Security / isolation
Untrusted code (repo tests, patched code) executes only inside the SWE-bench Docker containers. The engine itself never `exec`s repo code. Artifact REST endpoint path-joins are sanitized to the run directory. No secrets in any persisted file.

### 8.5 Testing the system itself
- Unit: patch extraction/validation, event schema round-trip, reducer fold determinism, reporter math (golden CSVs).
- Integration: one **stub-model** end-to-end test (canned responses; no API, no Docker — Docker grader mocked) that walks the full graph in <5 s; runs in CI.
- The stub model is also the frontend's dev harness: `run-batch --model stub` generates realistic event streams for building the garage without spending a cent.

---

## 9. Architecture Decision Records (summary)

| # | Decision | Alternatives rejected | Why |
|---|---|---|---|
| ADR-1 | JSONL event log as single source of truth | SQLite, Redis pub/sub | Replay = live file; crash-safe; greppable; scale is bounded (§4.2) |
| ADR-2 | LangGraph, hand-written graph | CrewAI, AutoGen | Explicit state machine the author can defend line-by-line; escape hatch: plain-Python state machine if it fights (R5) |
| ADR-3 | Official SWE-bench Docker harness | Custom pytest runner | Per-repo environments are the known project-killer (R1); grading credibility |
| ADR-4 | git worktree per attempt | Reset in place; full clone per attempt | Clean retries at near-zero cost |
| ADR-5 | Events carry pointers, artifacts on disk | Blobs in events | Tailable stream; lazy artifact fetch; small WS frames |
| ADR-6 | Engine/server split, filesystem interface | One process | Headless weeks 1–2; server restarts can't kill a batch run |
| ADR-7 | Simplicity gate ships-anyway at cap | Reviewer can kill a passing patch | A correct patch is never discarded for style; the verdict is still recorded for E3 analysis |
| ADR-8 | Sequential batch v1 | Parallel workers | Docker cache races + coherent visualization; revisit post-week-4 |
| ADR-9 | Waypoint paths, no pathfinding | A* grid | Week 4 is one week; waypoints are indistinguishable on a 6-station floor |
| ADR-10 | Frozen event schema `v:1` at week-3 exit | Evolve freely | Replay files must stay readable; unknown-type tolerance gives forward room |

---

## 10. Build Order Mapping

| Week | Modules built | Modules stubbed |
|---|---|---|
| 1 | `eval/`, `repo/workspace.py`, `repo/patch.py`, `agents/builder.py`, `cli run-one` | everything else; orchestrator = template, scout = "whole issue" |
| 2 | `graph.py`, `state.py`, `agents/{scout,tester,reviewer}.py`, `repo/{repomap,search}.py`, `accounting/`, `report/`, `cli run-batch` | `retrieval.py` (embeddings) behind flag |
| 3 | `events.py` (retrofit emits into existing nodes), `server/`, `web/feed/` | garage |
| 4 | `web/garage/` | replay |
| Later | `web/replay/`, `retrieval.py`, experiments E2–E4 | — |

Note on week 3: event emission is *retrofit* into a working engine, not built first — this is deliberate. The engine must be provably correct from `results.csv` alone before observability is layered on.
