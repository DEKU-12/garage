# AI Code Garage — Blueprint (source document)

> Converted from `AI_Code_Garage_Project_Blueprint.docx`. This is the
> **product vision**: a garage that repairs a user's own GitHub repo.
> Where it conflicts with `PRD.md` / `TAD.md`, [PLAN.md](PLAN.md) says
> which wins and why.

AI Code Garage
Project Blueprint & System Architecture

A multi-agent AI software repair, simplification, optimization, testing, and review system


## 1. Project Overview

AI Code Garage is a multi-agent AI engineering system in which a user provides a GitHub repository. Specialized AI agents inspect, debug, simplify, optimize, test, secure, and review the repository. The entire process is represented visually as a software garage: the repository is the car and each AI agent is a mechanic with a specific responsibility.

The visualization is not merely decorative. Backend events drive the garage animations, so what the user sees corresponds to real agent activity.


## 2. Core Objective

The system should transform:

GitHub Repository → AI Code Garage → Validated Improvements → Git Diff / Pull Request

The system should prioritize:

Correctness over line-count reduction

Simpler and more maintainable code

Measured performance improvements rather than unsupported claims

Automated testing and regression protection

Human approval before changes reach the main branch


## 3. High-Level Architecture

                             USER
                           │
                           │ GitHub URL
                           ▼
                  ┌─────────────────┐
                  │ FRONTEND        │
                  │ Garage UI       │
                  └────────┬────────┘
                           │ WebSocket / SSE
                           ▼
                  ┌─────────────────┐
                  │ BACKEND API     │
                  │ FastAPI         │
                  └────────┬────────┘
                           ▼
                  ┌─────────────────┐
                  │ ORCHESTRATOR    │
                  │ "FOREMAN"       │
                  └────────┬────────┘
                           │
          ┌────────────────┼────────────────┐
          ▼                ▼                ▼
       Scout           Debugger         Security
          │                │                │
          └────────────────┼────────────────┘
                           ▼
                    Simplifier
                    "Ponytail"
                           │
                           ▼
                       Optimizer
                           │
                           ▼
                        Tester
                           │
                           ▼
                       Reviewer
                           │
                           ▼
                    Git Diff / PR

## 4. Garage Workers / AI Agents

Foreman / Orchestrator: Coordinates the entire workflow. Decides which worker should act next, consumes reports from agents, manages retries, and determines when the repository is ready for final review.

Scout / Repository Inspector: Maps the repository, detects language/framework, identifies entry points and tests, analyzes dependencies, and discovers suspicious or complex areas.

Debugger: Investigates test failures and runtime errors, retrieves relevant code, proposes fixes, modifies files, and validates fixes through tests.

Simplifier / Ponytail-inspired Mechanic: Looks for unnecessary code, duplicate implementations, unnecessary abstractions, unused dependencies, and overly complex logic. It aims for simplicity, readability, and maintainability rather than blindly minimizing lines.

Performance Optimizer: Analyzes algorithmic complexity, repeated work, database/API calls, memory behavior, and other performance bottlenecks. Uses benchmarks or profilers to validate improvements.

Security Mechanic: Looks for secrets, unsafe input handling, dependency vulnerabilities, insecure commands, authentication issues, and other security problems. Specialized static-analysis tools should supplement the LLM.

Tester: Runs existing tests, generates useful missing tests, performs regression testing, checks coverage, and reports pass/fail results.

Senior Reviewer: Reviews the complete Git diff together with test, performance, and security results. Approves the patch or rejects it and sends it back for correction.


## 5. LLM Strategy

A separate LLM is not required for every garage worker. Agents are better understood as a combination of an LLM, a role-specific prompt, tools, permissions, context, memory, and a goal.

AGENT = LLM + System Prompt + Tools + Context + Permissions + Goal + Feedback Loop

Recommended first version:

One strong LLM serving multiple specialized agent roles.

Different prompts and tool permissions for each worker.

Use deterministic tools for parsing, linting, testing, Git, security checks, and benchmarking.

Later, introduce different models only when cost, latency, or specialization justifies it.


## 6. Codebase RAG / Retrieval

Large repositories should not be sent in full to every agent. Build a code-aware retrieval layer.

Repository
   ↓
Code Parser
   ↓
Functions / Classes / Modules / Documentation
   ↓
Embeddings + Metadata
   ↓
Vector / Retrieval Store
   ↓
Agent retrieves relevant files and symbols

Example: a debugger investigating JWT authentication might retrieve auth/token.py, auth/middleware.py, api/login.py, and related tests rather than the entire repository.


## 7. Agent Tooling

read_file()

search_code()

write_file()

run_command()

run_test()

git_diff()

git_status()

repository_tree()

AST/code parser

benchmark/profiler

linter/type checker

security scanners


## 8. Agent Workflow

Repository received
        ↓
Create isolated Git branch/worktree
        ↓
Scout analyzes repository
        ↓
Foreman prioritizes issues
        ↓
Debugger fixes correctness problems
        ↓
Simplifier removes unnecessary complexity
        ↓
Optimizer measures and improves performance
        ↓
Security checks repository
        ↓
Tester runs regression tests
        ↓
Reviewer evaluates complete diff
        ↓
PASS → Pull Request
FAIL → Rollback / targeted agent retry


## 9. Testing and Validation

Every meaningful code modification should be validated. The system should never treat an LLM statement such as 'this should work' as proof.

Code change
    ↓
Run tests
    ↓
PASS ─────────────→ Continue
    │
    FAIL
    ↓
Debugger / relevant agent
    ↓
Fix
    ↓
Test again

Possible final metrics:

Tests passed / total

Test coverage

Cyclomatic or cognitive complexity

Duplicated-code percentage

Lines of code (reported as a secondary metric)

Static-analysis findings

Security findings

Benchmark results


## 10. Git and Safety Architecture

Agents should never directly modify the user's main branch.

main
 │
 └── ai-garage/session-001
        ├── Scout
        ├── Debugger
        ├── Simplifier
        ├── Optimizer
        └── Tester
                 ↓
             Evaluation
                 ↓
          Pull Request

The repository should be executed in an isolated environment such as a restricted Docker container. The system should limit permissions and destroy the execution environment after the session.


## 11. Event-Driven Garage Visualization

The backend emits events such as:

AGENT_STARTED

FILE_READ

BUG_FOUND

CODE_CHANGED

TEST_STARTED

TEST_FAILED

TEST_PASSED

AGENT_FINISHED

REVIEW_APPROVED

PR_CREATED

The frontend maps those events to visual actions:

AGENT_STARTED("debugger")
        ↓
🐛 Debugger walks to 🚗 and starts working

TEST_STARTED
        ↓
🧪 Tester drives 🚗 onto the test track

TEST_FAILED
        ↓
🚨 Garage alert + Debugger returns

TEST_PASSED
        ↓
✅ Tester finishes and Foreman advances workflow


## 12. Frontend / Garage

Recommended frontend stack:

Next.js / React

TypeScript

WebSocket or Server-Sent Events for live activity

PixiJS or another 2D rendering approach for the animated garage

Dashboard for logs, diffs, metrics, and agent status

Garage concepts:

Repository = car

Scout = inspection mechanic

Debugger = repair mechanic

Simplifier = senior mechanic focused on removing unnecessary complexity

Optimizer = performance mechanic

Tester = test-track mechanic

Reviewer = senior supervisor


## 13. Backend Project Structure

ai-code-garage/
├── frontend/
│   ├── garage/
│   ├── characters/
│   ├── animations/
│   └── dashboard/
│
├── backend/
│   ├── api/
│   ├── orchestrator/
│   ├── agents/
│   │   ├── scout.py
│   │   ├── debugger.py
│   │   ├── simplifier.py
│   │   ├── optimizer.py
│   │   ├── security.py
│   │   ├── tester.py
│   │   └── reviewer.py
│   ├── tools/
│   ├── retrieval/
│   ├── sandbox/
│   └── events/
│
├── evaluation/
└── docker/


## 14. Suggested Technology Stack


## 15. Development Roadmap

    Phase 1 — MVP: GitHub URL → clone repository → Scout → show findings in garage.
Phase 2 — Simplification: Add Simplifier/Ponytail-inspired worker → generate diff → validate changes.

Phase 3 — Testing: Run tests automatically and handle pass/fail loops.

Phase 4 — Debugging: Add Debugger with iterative fix/test cycle.

Phase 5 — Optimization: Add profiling and benchmark-based performance changes.

Phase 6 — Security + Review: Add security checks and Senior Reviewer.

Phase 7 — RAG: Add repository-aware retrieval for larger codebases.

Phase 8 — Visualization Polish: Add detailed character animations, garage states, activity logs, diffs, and final metrics.


## 16. Final User Experience

1. User pastes GitHub URL.
2. Garage opens and the repository arrives as a car.
3. Scout inspects the repository.
4. Foreman creates a work plan.
5. Debugger repairs correctness problems.
6. Simplifier removes unnecessary complexity.
7. Optimizer investigates measurable performance improvements.
8. Security worker checks for vulnerabilities.
9. Tester validates every meaningful change.
10. Senior Reviewer evaluates the final patch.
11. System displays before/after metrics.
12. System creates a Git diff / Pull Request.
13. User decides whether to merge.

The strongest version of the project makes the visualization truthful: every important garage animation corresponds to a real backend event and every claimed improvement is backed by an actual measurement.


## 17. Portfolio Value

If fully implemented, the project can demonstrate agent orchestration, LLM tool use, code-aware retrieval/RAG, software engineering, Git workflows, sandboxed execution, automated evaluation, backend development, real-time systems, and an original visualization layer.

For AI Engineer / GenAI / LLM / Agentic AI fresher roles, the project has strong portfolio potential. For traditional ML Engineer roles, it should ideally be accompanied by a separate project demonstrating model training and rigorous ML evaluation.


## 18. Design Principle

Every animation should correspond to a real backend event.

The garage should not be a fake animation placed on top of an LLM demo. It should be a visual window into a real autonomous software-engineering system.

