# Product Requirements Document
## Multi-Agent Coding System with Eval Gates and Visual Tracing
**Working title:** "The Garage"

| | |
|---|---|
| **Author** | (you) |
| **Status** | Draft v1.0 |
| **Date** | August 2026 |
| **Project type** | Portfolio / research project |
| **Target completion** | 4 weeks core + 2–3 weeks extensions |

---

## 1. Executive Summary

A multi-agent system that fixes real bugs in real codebases (SWE-bench Lite), where every patch must pass **two quality gates** — a correctness gate (tests) and a simplicity gate (anti-over-engineering review) — before it ships. The system emits a timestamped event stream that powers a real-time 2D pixel-art visualization (a "startup garage") and a time-travel replay debugger.

The differentiator is not the pixel art. It is that the system **grades its own output and reports a success rate** — something no comparable open-source multi-agent visualization project (ChatDev, MetaGPT, AI Town, Munder Difflin) does.

**The single sentence this project must be able to say when done:**

> "Task success on SWE-bench Lite went from X% to Y% when the eval loop was turned on, at $Z per solved task."

---

## 2. Background & Problem Statement

### 2.1 The field problem
Multi-agent LLM systems are widely demoed and rarely measured. Teams wire up agents with roles and personalities, watch them exchange messages, and never establish whether the multi-agent configuration outperforms a single model called once. Shipping agent systems without evals is a recognized, ongoing failure mode in the industry.

### 2.2 The gap in prior art
| Project | Coordinates agents | Visualizes | **Grades output** | **Reports success rate** |
|---|---|---|---|---|
| ChatDev / MetaGPT | ✅ | ✅ (replay logs) | ❌ | ❌ |
| Smallville / generative_agents | ✅ | ✅ | ❌ | ❌ |
| AI Town / claw-pixel-town | ✅ | ✅ | ❌ | ❌ |
| Munder Difflin | ✅ | ✅ | ❌ | ❌ |
| **This project** | ✅ | ✅ | ✅ | ✅ |

### 2.3 The open research question
The **ponytail** ruleset (github.com/DietrichGebert/ponytail, ~104k stars) claims 54–94% less code and 22–77% lower cost — but was measured only on toy tasks (email validator, debounce, CSV sum, countdown, rate limiter). **Nobody has tested whether "write less code" helps or hurts when a real test suite on real GitHub issues is grading correctness.** This project produces the first A/B answer.

### 2.4 The personal problem
The author is a new grad targeting entry-level AI engineering roles. The project must produce **defensible, measurable results** suitable for a resume line and a technical interview — not just a demo.

---

## 3. Goals & Success Metrics

### 3.1 Primary metrics (must produce)
| ID | Metric | Definition | Target |
|---|---|---|---|
| M1 | **Gate lift** | % tasks solved, eval gate ON vs OFF, same tasks, same model | Statistically visible lift on ≥30 tasks (e.g., 34% → 51%) |
| M2 | **Cost per solved task** | Total API spend ÷ number of *solved* tasks | Reported for every configuration |
| M3 | **Avg iterations per solve** | Mean builder attempts on solved tasks | Reported (expected ~2) |

### 3.2 Secondary metrics (should produce)
| ID | Metric | Definition |
|---|---|---|
| M4 | **Scout lift** | % solved with retrieval-based context selection ON vs OFF, plus token delta |
| M5 | **Ponytail effect** | % solved + median patch size, Reviewer ON vs OFF |
| M6 | **Model bake-off table** | Success % and $/solved for role-model permutations (big judge + small builder, etc.) |

### 3.3 Non-metric goals
- G1: A live visualization that renders **only from the event stream** (no privileged access to internal state) — proving the stream is complete.
- G2: A replay scrubber that can re-enact any run from its JSONL log.
- G3: Codebase clean enough to walk an interviewer through in 10 minutes.

### 3.4 Explicit non-goals
- **NG1:** Competing with commercial coding agents (Devin, Claude Code, Copilot Workspace) on absolute solve rate.
- **NG2:** Supporting arbitrary user repos as a product. SWE-bench Lite tasks only, for the core project.
- **NG3:** Multi-user, deployment, auth, or hosting. This runs locally.
- **NG4:** Novel model training or fine-tuning. Off-the-shelf models only.
- **NG5:** Copying Munder Difflin's Office theme or LimeZu assets (non-commercial license). Theme is an original startup garage; sprites are Kenney.nl CC0.

---

## 4. Users & Use Cases

| User | Use case |
|---|---|
| **The author** (primary) | Runs benchmarks, watches live runs, debugs agent handoffs via replay, produces the metrics tables |
| **Interviewer / hiring manager** | Watches a 2-minute demo; inspects the README's results table; asks "why did task 17 fail?" and gets an answer via replay |
| **GitHub visitor** | Clones, runs one task end-to-end with their own API key in <10 minutes |

---

## 5. System Overview

### 5.1 The cast (6 agents, same model, different system prompts)

| # | Agent | Responsibility | Authority | Garage station |
|---|---|---|---|---|
| 1 | **Orchestrator** | Parse the issue, route work, own the state machine, enforce retry caps | Assigns; never writes code | Whiteboard |
| 2 | **Scout** | Produce the "context pack": relevant files/functions, call sites, related tests | Read-only on the repo | Filing cabinet |
| 3 | **Builder** | Write the patch as a unified diff against a clean checkout | Writes code; cannot ship | Triple-monitor desk |
| 4 | **Tester** | Apply patch, run the repo's designated tests, parse results | **Can reject** (correctness), max 3 retries | Test bench |
| 5 | **Reviewer** | Apply the ponytail ladder; reject over-engineered patches with a reason | **Can reject** (simplicity), max 1 retry | Workbench |
| 6 | **Scribe** | Write the run summary / changelog entry | None (cosmetic; cut first if behind schedule) | Side desk |

Idle agents sit on **the couch**.

**Minimum viable cast:** Orchestrator, Scout, Builder, Tester (4). Reviewer and Scribe are additive.

### 5.2 The core loop

```
issue → orchestrator → scout builds context pack
                            ↓
                     builder writes patch
                            ↓
                     tester applies patch, runs tests
                      ↓                ↓
                    fail             pass
                      ↓                ↓
          back to builder        reviewer checks simplicity
          with failure log        ↓                ↓
          (max 3 retries)      reject            pass
                                  ↓                ↓
                        back to builder          SHIP
                        with reason              (record metrics,
                        (max 1 retry)             scribe writes summary)
```

Two gates measure **opposite pressures** (correctness wants more code; simplicity wants less). Separate retry caps (3 correctness / 1 simplicity) guarantee termination.

### 5.3 The Reviewer's ladder (adapted from ponytail)
Rejection reasons, checked in order:
1. Does this code need to exist at all? (Is the fix a deletion?)
2. Does the codebase already contain this?
3. Does the standard library provide this?
4. Does a native platform feature provide this?
5. Does an already-installed dependency provide this?
6. Can it be one line?

Output: `ACCEPT` or `REJECT` + which rung + one-paragraph reason (fed back to Builder).

---

## 6. Functional Requirements

Priority key: **P0** = core, project fails without it · **P1** = strongly expected · **P2** = extension.

### 6.1 Harness (Week 1)
| ID | Requirement | Priority |
|---|---|---|
| FR-1 | Load a single SWE-bench Lite task (repo, base commit, issue text, fail-to-pass tests) via the official `swebench` package | P0 |
| FR-2 | Clone/checkout the target repo at the base commit into an isolated working directory (fresh checkout or git worktree per attempt) | P0 |
| FR-3 | Call a model with issue + context and receive a patch (unified diff) | P0 |
| FR-4 | Apply the patch; on apply failure, record it as a distinct failure type (`patch_apply_error`) and retry | P0 |
| FR-5 | Run the task's designated tests via the official SWE-bench Docker evaluation harness; parse pass/fail | P0 |
| FR-6 | Persist per-attempt artifacts: prompt, raw response, patch, test output, timing, token counts | P0 |

### 6.2 Orchestration & gates (Week 2)
| ID | Requirement | Priority |
|---|---|---|
| FR-7 | LangGraph state machine implementing the loop in §5.2, with state = {task, context_pack, attempts[], gate_verdicts[], status} | P0 |
| FR-8 | Tester gate: reject with the failing test output attached; enforce max 3 correctness retries | P0 |
| FR-9 | Reviewer gate: ladder verdict with reason; enforce max 1 simplicity retry | P1 |
| FR-10 | Gate bypass flags (`--no-tester-gate`, `--no-reviewer-gate`, `--no-scout`) so every A/B in §3 is one CLI flag | P0 |
| FR-11 | Batch runner: execute N tasks sequentially or with bounded parallelism; resumable after crash (skip completed tasks) | P0 |
| FR-12 | Per-task and per-run budget caps (max tokens / max $); exceeding a cap marks the task `budget_exceeded`, not `crashed` | P1 |

### 6.3 Scout / retrieval (Week 2)
| ID | Requirement | Priority |
|---|---|---|
| FR-13 | Repo map: file tree + function/class signatures (AST or tree-sitter), given to Scout as cheap context | P0 |
| FR-14 | Keyword search over the repo (ripgrep) callable by the Scout | P0 |
| FR-15 | Embedding index (code chunked by function/class, Chroma) as a second retrieval channel; measured against grep-only (metric M4) | P1 |
| FR-16 | Context pack format: ordered list of {file, line-range, why} within a hard token budget | P0 |

### 6.4 Event stream (Week 3)
| ID | Requirement | Priority |
|---|---|---|
| FR-17 | Every state change emits one JSONL event: `{ts, run_id, task_id, agent, event_type, payload}` — event types include `task_started`, `agent_activated`, `handoff`, `patch_produced`, `tests_run`, `gate_verdict`, `retry`, `shipped`, `failed` | P0 |
| FR-18 | Events are the **single source of truth** for all UI; nothing renders from internal state | P0 |
| FR-19 | FastAPI backend serving (a) WebSocket live stream, (b) REST endpoint to fetch a past run's full event log | P0 |
| FR-20 | Plain-text live feed view in the browser (pre-pixel-art) | P0 |

### 6.5 Visualization (Week 4)
| ID | Requirement | Priority |
|---|---|---|
| FR-21 | Pixi.js garage scene: 6 stations + couch, original layout, Kenney.nl CC0 sprites | P0 |
| FR-22 | Agent avatars walk to their station when activated, return to couch when idle; driven only by events | P0 |
| FR-23 | Handoffs visualized (e.g., a paper/envelope moving between stations) | P1 |
| FR-24 | Gate verdicts visibly distinct (e.g., green stamp / red stamp animation at the bench) | P1 |
| FR-25 | Live status HUD: current task, attempt #, tokens/cost so far | P1 |

### 6.6 Replay & debugging (Later)
| ID | Requirement | Priority |
|---|---|---|
| FR-26 | Load any past run's JSONL and re-enact it in the garage | P2 |
| FR-27 | Scrubber: drag to any timestamp; scene reconstructs state at that moment | P2 |
| FR-28 | Hover/click an agent during replay → see the actual prompt and response for that step | P2 |
| FR-29 | Cost overlay: cumulative $ line chart synced to the scrubber | P2 |

### 6.7 Metrics & reporting
| ID | Requirement | Priority |
|---|---|---|
| FR-30 | Per-run results table (CSV + markdown): task_id, solved, attempts, tokens, $, wall time, failure type | P0 |
| FR-31 | Aggregate report generator: computes M1–M6 from raw run logs; the README table is generated, never hand-typed | P0 |
| FR-32 | Token/cost accounting from API usage fields, priced per model | P0 |

---

## 7. Non-Functional Requirements

| ID | Requirement |
|---|---|
| NFR-1 | **Reproducibility:** every reported number regenerable from committed run logs + one command. Fixed model versions, temperature, and prompts per experiment; prompts version-controlled |
| NFR-2 | **Determinism boundaries documented:** LLM outputs vary; report per-config runs on the same task set, and note run-to-run variance if multiple passes are done |
| NFR-3 | **Isolation:** repo checkouts and test execution never touch the host environment (SWE-bench Docker harness for tests) |
| NFR-4 | **Crash safety:** batch runs resume; a crashed task is recorded, not silently dropped |
| NFR-5 | **Cost ceiling:** a full 30–50 task experiment must be runnable for a defined budget (estimate before running; Groq-class pricing keeps this in single-digit dollars) |
| NFR-6 | **Latency (viz):** event-to-pixel under ~250 ms on live runs — enough to feel real-time |
| NFR-7 | **Setup time:** README quickstart gets a stranger from clone to one completed task in <10 minutes (excluding Docker image pulls) |
| NFR-8 | **Licensing:** all bundled assets CC0 or equivalently permissive; code MIT |

---

## 8. Architecture & Stack

```
┌────────────────────────────────────────────────────┐
│                    Frontend (React)                 │
│   ┌──────────────┐   ┌──────────────────────────┐  │
│   │ Garage scene │   │ Text feed / HUD / replay │  │
│   │  (Pixi.js)   │   │        controls          │  │
│   └──────▲───────┘   └────────────▲─────────────┘  │
└──────────┼────────────────────────┼────────────────┘
           │        WebSocket / REST│
┌──────────┴────────────────────────┴────────────────┐
│                 FastAPI event server                │
│        (live broadcast + run-log retrieval)         │
└──────────────────────▲──────────────────────────────┘
                       │ JSONL events (single source of truth)
┌──────────────────────┴──────────────────────────────┐
│              LangGraph state machine                 │
│  orchestrator → scout → builder → tester → reviewer  │
│        retry edges · gate caps · budget caps         │
└───────┬──────────────────┬───────────────────────────┘
        │ model calls      │ repo ops / tests
┌───────┴────────┐  ┌──────┴───────────────────────────┐
│  Groq API      │  │ git checkout per attempt          │
│  (gpt-oss-20b/ │  │ ripgrep · AST repo map · Chroma   │
│   120b)        │  │ SWE-bench Docker eval harness     │
└────────────────┘  └───────────────────────────────────┘
```

| Layer | Choice | Rationale |
|---|---|---|
| Orchestration | **LangGraph** | Explicit, inspectable state machine; author writes the graph by hand (interview-defensible) |
| Models | **Groq API** (`openai/gpt-oss-20b`, `120b`) | Cheap + fast enough for 50-task batches; role-swappable for bake-off |
| Benchmark | **SWE-bench Lite** (300 tasks; use 30–50 subset) | Real repos, real issues, official grading harness |
| Test execution | **Official `swebench` Docker harness** | Per-repo environments are the known time sink; do not roll your own |
| Retrieval | ripgrep + AST repo map (P0), Chroma embeddings (P1) | Cheap tools first; embeddings must *earn* their place via M4 |
| Backend | **FastAPI + WebSockets** | |
| Frontend | **React + Pixi.js** | |
| Sprites | **Kenney.nl (CC0)** | Commercial-safe; deliberately not LimeZu |
| Env | macOS, Python 3.12, **uv**, keys in `.env` (`load_dotenv(override=True)`), LangChain 1.3.x import paths (`langchain_core` / `langchain_community`) | Author's environment |

---

## 9. Milestones & Exit Criteria

| Week | Deliverable | Exit criterion (binary) |
|---|---|---|
| **1 — Harness** | FR-1…FR-6. No UI, no LangGraph needed yet | **One** SWE-bench task runs end-to-end: issue in → patch out → Docker tests → recorded pass/fail |
| **2 — Loop + the number** | FR-7…FR-16. Scout, gates, retries, batch runner, flags | 30–50 tasks run twice (gate OFF, gate ON); **M1 table exists** |
| **3 — Event stream** | FR-17…FR-20 | A live run streams to a browser text feed; a saved run replays as text |
| **4 — Garage** | FR-21…FR-25 | Avatars re-enact a live run correctly, driven only by events |
| **Later** | FR-26…FR-29 + M5 ponytail A/B + M6 bake-off | Scrubber works; ponytail table published |

**Rule:** Week 4 does not start until Week 2's number exists. The visualization is built on top of a result, not instead of one.

---

## 10. Experiment Plan

All experiments: same task subset, same prompts (except the variable), fixed temperature, results generated by FR-31.

| Exp | Variable | Configs | Metric |
|---|---|---|---|
| E1 | Tester gate | OFF vs ON | M1, M2, M3 |
| E2 | Scout | OFF (whole-issue naive) vs grep+map vs +embeddings | M4 |
| E3 | Reviewer (ponytail) | OFF vs ON | M5: solve % + median patch LOC + $ |
| E4 | Model-per-role | big builder+small judge / small builder+big judge / uniform | M6 |

**E3 is the publishable finding.** Possible outcomes, all interesting: ponytail preserves solve rate with smaller patches (claim survives contact with real tests) · ponytail lowers solve rate (minimalism trades against correctness) · no effect (toy-task claims don't transfer).

---

## 11. Risks & Mitigations

| # | Risk | Likelihood | Mitigation |
|---|---|---|---|
| R1 | SWE-bench environment setup eats week 1 | **High** | Use official Docker harness from day 1; fallback: HumanEval+ for the loop, SWE-bench later |
| R2 | Small models solve ~0 tasks → no gate lift measurable | Medium | Pre-screen 10 tasks; if solve rate <10%, pick the easiest SWE-bench Lite subset or drop to HumanEval for M1 and keep SWE-bench for a smaller table |
| R3 | Patch apply failures dominate | Medium | Strict diff-format prompt + retry-on-apply-error as its own loop; count separately (FR-4) |
| R4 | Cost blowout on retries | Low | FR-12 budget caps; Groq pricing |
| R5 | LangGraph fights the design | Medium | Graph is ~6 nodes; if it fights, hand-rolled state machine is acceptable — the graph is not the point, the gates are |
| R6 | Week 4 temptation | High | §9 rule; text feed (FR-20) is the demo until the number exists |
| R7 | Variance makes M1 look noisy | Medium | ≥30 tasks; report counts not just %; same seed/temp; note variance |

---

## 12. Out of Scope (explicitly)

- Arbitrary/user-supplied repos, GitHub App integration, PR automation
- Human-in-the-loop approvals, multi-user features, deployment
- Terminal-CLI agent wrapping (node-pty style) — API calls only for the core project
- Memory across tasks / long-term agent memory
- Fine-tuning, RLHF, custom models

---

## 13. Open Questions

| # | Question | Decide by |
|---|---|---|
| Q1 | HumanEval fallback threshold: how low must the SWE-bench solve rate be before switching M1 to HumanEval? | End of week 1 |
| Q2 | Reviewer sees diff-only or diff + surrounding file context? (Affects both cost and verdict quality) | Week 2 |
| Q3 | Parallel task execution in the batch runner, or sequential-only for v1? | Week 2 |
| Q4 | Does the Scribe survive, or is it cut for schedule? | Week 3 |
| Q5 | Replay format versioning — freeze the event schema (FR-17) before or after week 4? | Week 3 |

---

## 14. Appendix

### A. Resume line this project must be able to back up
> Multi-agent code generation system (LangGraph, Python, Pixi.js): router + specialist agents with dual correctness/simplicity gates and bounded retries. X% → Y% task success on SWE-bench Lite by adding the eval loop; N avg iterations per solved task; built a live visual tracer for debugging agent handoffs.

*(Numbers to be filled from E1 — never estimated.)*

### B. Prior art positioning
ChatDev/MetaGPT (role simulation, replay), Stanford generative_agents (pixel village origin), AI Town / claw-pixel-town (visual match), Munder Difflin (terminal-CLI agents, file-based hive, main inspiration — Office theme and LimeZu assets deliberately not reused). Differentiation: the eval loop and published success metrics.

### C. Interview framing
"Multi-agent demos usually can't tell you if they work. I built one that grades itself — and I can tell you exactly what the eval gate is worth, what retrieval is worth, and whether a 104k-star 'write less code' ruleset survives contact with a real test suite."
