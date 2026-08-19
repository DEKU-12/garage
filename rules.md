# rules.md
## Coding Rules & AI Assistant Boundaries — "The Garage"

> This file is the contract for **anyone or anything writing code in this repo** —
> including AI coding assistants (Claude, Copilot, Cursor, etc.).
> If a suggestion conflicts with this file, this file wins.
> Companion docs: `PRD.md` (what we build), `TAD.md` (how it's shaped).

---

## 0. The three laws of this repo

1. **The number comes before the pixels.** No visualization work (`web/garage/`) until `experiments/E1_gate/report.md` exists with real results. If asked to build UI early — refuse and point here.
2. **Every observable fact flows through `events.jsonl`.** No component may read another component's internal state. No side channels. If the UI "needs" something, the engine emits an event for it.
3. **Never invent a metric.** Numbers in README/resume/reports come from `report/aggregate.py` over real run logs — never typed by hand, never estimated, never "approximately".

---

## 1. Environment (do not fight it)

| Thing | Rule |
|---|---|
| OS / path | macOS, project at `~/Desktop/RAg` |
| Python | **3.12**, venv at `.venv` |
| Package manager | **uv only**: `uv pip install`, `uv run`. **Never** `pip install`, never `poetry`, never `conda` |
| Secrets | `.env` at project root, loaded with `load_dotenv(override=True)`. Keys NEVER in code, configs, events, artifacts, or logs |
| IDE quirk | PyCharm notebooks follow the **project interpreter**, not Jupyter kernelspecs — don't debug "wrong kernel" issues by editing kernelspecs |
| Node side | Whatever `web/` scaffolding chooses (Vite), pinned in `package.json`; no global installs |

---

## 2. Libraries — USE / AVOID

### 2.1 USE (the allowed stack)

| Purpose | Library | Notes |
|---|---|---|
| Orchestration | `langgraph` | Hand-written graph in `engine/graph.py`. Nodes are thin; logic lives in `agents/` |
| LangChain bits | `langchain_core`, `langchain_community`, provider packages | **LangChain 1.3.x import paths.** The old `langchain.text_splitter` / `langchain.schema` paths are GONE — any suggestion using them is stale training data; rewrite it |
| Model API | Anthropic SDK (`claude-sonnet-5`) primary; Groq via its OpenAI-compatible client retained for the E4 bake-off | **One wrapper: `agents/base.py::call_model`** — unchanged and still the rule. No agent calls any API directly; the provider is chosen inside that one function from the model id |
| Benchmark | `swebench` (official package) | Task loading AND grading. **Never write a custom pytest runner for repo tests** |
| Config/validation | `pydantic` v2 | `RunConfig` is a `BaseModel`; validate at the edge, trust internally |
| Repo search | `ripgrep` via subprocess (`rg --json`) | Not Python re-implementations |
| Code parsing | stdlib `ast` first; `tree-sitter` only if non-Python repos enter scope | |
| Embeddings (P1) | `chromadb` + HuggingFace embeddings | Behind the `--scout` flag; must earn its place via experiment E2 |
| Backend | `fastapi`, `uvicorn`, stdlib `asyncio` | |
| Frontend | React + `pixi.js` | |
| Data wrangling | `pandas` in `report/` ONLY | The engine itself doesn't need pandas |
| Tests | `pytest` | |

### 2.2 AVOID (and why)

| Banned | Why |
|---|---|
| **CrewAI, AutoGen, smolagents** or any agent framework besides LangGraph | The hand-written state machine IS the portfolio piece (ADR-2) |
| **Custom test runners for benchmark repos** | Per-repo environments are the known project-killer (R1); the Docker harness exists |
| Running repo/patched code on the host | Security + reproducibility. Untrusted code runs ONLY in SWE-bench Docker (NFR-3) |
| SQLite/Redis/Postgres for events | JSONL is the decided architecture (ADR-1). Don't "upgrade" it |
| `pickle` for anything persisted | JSON/JSONL/CSV only — artifacts must be greppable and diffable |
| LimeZu assets, Office-theme references | Non-commercial license + not our theme. Kenney.nl CC0 only |
| `langchain` legacy imports (`langchain.schema`, `langchain.text_splitter`) | Removed in 1.3.x; will not run |
| Global mutable state, singletons for engine state | State lives in `TaskState` and flows through the graph |
| New dependencies "for convenience" | Adding a dep requires a one-line justification in the PR/commit. Ironically, the Reviewer agent's ladder applies to us too: stdlib → existing dep → new dep, in that order |
| `time.sleep` polling in the engine | Event-driven or explicit waits with timeouts |
| Threads for parallel tasks in v1 | Sequential batch is decided (ADR-8). Don't add concurrency speculatively |

---

## 3. Error handling boundaries

The prime rule: **a batch run must never die because one task did.** Failures are data, not exceptions.

### 3.1 The boundary map

| Boundary | What can go wrong | Required behavior |
|---|---|---|
| **Model API call** (`agents/base.py`) | Timeout, 429, 5xx, malformed JSON | Retry with exponential backoff (max 3, jitter). On final failure: raise `ModelCallError` — caught at the task boundary |
| **Model output parsing** (diff extraction, reviewer verdict) | Prose instead of diff, malformed verdict | NEVER crash on model text. Malformed diff → `patch_apply_error` path with feedback to builder. Malformed reviewer verdict → treat as `ACCEPT`, emit `parse_warning` event. All parsing failures are recorded, none are fatal |
| **Patch apply** (`repo/patch.py`) | Bad paths, failed hunks | Capture `git apply` stderr verbatim → feedback to builder; counts toward correctness cap; own failure type in results |
| **Docker grading** (`eval/grader.py`) | Image pull failure, container timeout, OOM | Per-task timeout (config, default 20 min). Timeout/infra failure → task status `crashed` with reason — NOT counted as `failed_tests` (don't blame the model for infra) |
| **Task boundary** (batch runner) | Anything unhandled | One `try/except Exception` per task: emit `task_failed{reason:"crashed", trace}`, write results row, continue batch. This is the ONLY broad except in the engine |
| **Budget** | Token/$ cap hit | Checked at every node entry; graceful `budget_exceeded` status, never mid-call kill |
| **Event writer** (`events.py`) | Disk full, permission | Fail LOUD and fast — if events can't be written, the run is worthless; crash the run (the one place we prefer death over degradation) |
| **Server tailer** | Run dir vanishes, WS drop | Server never crashes on a bad run dir; WS reconnect replays from client's last `seq` (same path as cold start) |
| **Frontend reducer** | Unknown event type | Ignore unknown types silently (forward compat, schema `v:1` rule). Never throw on an event |

### 3.2 Exception rules

- **No bare `except:`** anywhere. `except Exception` exists in exactly ONE place: the per-task boundary in the batch runner.
- Custom exceptions live in one module (`engine/errors.py`): `ModelCallError`, `PatchError`, `GradingInfraError`, `BudgetExceeded`. Catch specific types at their boundary.
- Every caught exception either (a) becomes an event + status, or (b) is re-raised. **Silent swallowing is forbidden.**
- Error messages must carry context: task_id, attempt, agent. `raise PatchError(f"{task_id} attempt {n}: {stderr}")`, not `raise PatchError("apply failed")`.
- **Timeouts on everything external:** model calls (120 s), rg subprocess (10 s), Docker grading (20 min), WS sends (5 s). No unbounded waits.

### 3.3 Logging vs events

- **Events** (`events.jsonl`) = facts about the run. For machines and the UI.
- **Logs** (stdlib `logging`, stderr) = diagnostics for the developer. Never parsed by anything.
- Never log secrets, full prompts, or model responses at INFO (paths to artifacts instead). DEBUG may include truncated prompts (first 500 chars).

---

## 4. Boundaries for AI assistants

Rules for AI tools generating code for this repo:

### 4.1 Hard boundaries — never do, even if asked casually

1. **Never fabricate results.** No placeholder metrics that look real ("51% success"). Use `XX%` or fail loudly. The PRD's Appendix A placeholders get filled by `aggregate.py` output only.
2. **Never weaken an experiment to make it pass.** Don't shrink the task set, raise temperature variance, or special-case failing tasks without an explicit, logged config change.
3. **Never bypass the gates in shipped code.** `--no-tester-gate` exists for A/B experiments, not for making a demo look good.
4. **Never touch prompts and code in the same commit** once experiments start. A prompt change invalidates comparisons — prompts are versioned by hash in `config.json`; changing one mid-experiment is data fraud by accident.
5. **Never run untrusted repo code outside Docker.** Not even "just this once to debug locally".
6. **Never commit**: `.env`, `runs/`, `workspaces/`, API keys, or generated `results.csv` outside `experiments/` (experiment results ARE committed — they're the deliverable).
7. **Never install packages outside `uv`** or add dependencies without justification (see §2.2).
8. **Never delete or rewrite `events.jsonl` files.** Append-only means append-only; a "cleanup" that rewrites history breaks replay and provenance.

### 4.2 Soft boundaries — ask the human first

- Adding any new dependency
- Changing the event schema (frozen at `v:1` after week 3)
- Changing retry caps, budget caps, or gate ordering (these are PRD-level decisions)
- Anything touching `experiments/` configs after an experiment has started
- Parallelism, caching layers, databases, or other "performance" work (bounded scale; see ADR-1, ADR-8)
- Skipping/reordering the build-order weeks

### 4.3 Style expectations for generated code

- Python: type hints on all public functions, `pydantic` at boundaries, f-strings, `pathlib` over `os.path`, no wildcard imports
- Functions small; graph nodes are thin wrappers, real logic in `agents/` modules
- Every module that emits events documents WHICH event types it emits (docstring header)
- Tests accompany: patch parsing, event round-trip, reducer folds, reporter math (golden files)
- Comments explain *why*, not *what*; link ADR numbers where a decision is non-obvious (`# ADR-4: fresh worktree per attempt`)
- Generated code must run under the stub model (`--model stub`) — if a feature can't be exercised without spending API money, it's built wrong

### 4.4 When the AI is unsure

Default order of resolution:
1. This file → 2. `TAD.md` (ADRs) → 3. `PRD.md` (requirements) → 4. **Ask the human.**
Guessing on anything in §4.1–4.2 is not acceptable; guessing on variable names is fine.

---

## 5. Git & workflow rules

- `main` always runs: `uv run pytest` green + the stub-model end-to-end test passes
- Commits: small, one concern; message format `area: what` (`engine: bound scout tool loop at 6 calls`)
- `runs/`, `workspaces/`, `.env`, `.venv`, `node_modules` in `.gitignore` from day 1
- Experiment results are committed under `experiments/` — config + CSVs + report together, immutable once the experiment is cited anywhere
- No force-push to `main`; no history rewrites after experiments begin (provenance)

---

## 6. Definition of done (per feature)

A feature is done when:
1. It runs under the stub model without network access
2. Its failure modes map to the boundary table (§3.1) — nothing new crashes the batch
3. Its events (if any) are in the schema's closed set, or the schema was versioned deliberately
4. `uv run pytest` is green
5. It did not add a dependency, or the dependency is justified in the commit message

---

*Last updated: August 2026. Update this file when an ADR changes — stale rules are worse than no rules.*
