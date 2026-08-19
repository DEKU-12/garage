"""FastAPI event server (TAD §5.1, FR-19).

Build week: 3.

Reads the filesystem and nothing else. It never imports the engine and the
engine never imports it (ADR-6): they meet at runs/<id>/events.jsonl. That is
what lets the engine run headless, and what stops a server restart from killing
a batch that is halfway through 30 tasks.

    GET  /api/runs                          list runs, newest first
    GET  /api/runs/{id}/events?after_seq=   full or incremental log
    GET  /api/runs/{id}/artifacts/{path}    lazy artifact fetch, sandboxed
    WS   /ws/live/{id}?after_seq=           catch up, then stream live
    GET  /                                  the text feed (FR-20)

The server never crashes on a bad run dir (rules.md §3.1): a missing run is a
404, not a traceback.
"""

from __future__ import annotations

import json
from pathlib import Path

from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse, PlainTextResponse

from engine.events import LOG_NAME, read_events
from server.tailer import run_dirs, tail_events

REPO_ROOT = Path(__file__).resolve().parents[1]
RUNS_ROOT = REPO_ROOT / "runs"
WEB_ROOT = REPO_ROOT / "web"

app = FastAPI(title="The Garage — event server")


def _run_dir(run_id: str) -> Path:
    """Resolve a run id to a directory, refusing anything outside runs/.

    `run_id` arrives from the URL, so it is untrusted: `../../etc` must not
    escape. Resolve first, then check containment -- checking the raw string
    misses symlinks and encoded traversal.
    """
    candidate = (RUNS_ROOT / run_id).resolve()
    if not candidate.is_relative_to(RUNS_ROOT.resolve()) or not candidate.is_dir():
        raise HTTPException(status_code=404, detail=f"no run {run_id!r}")
    return candidate


@app.get("/api/runs")
def list_runs() -> list[dict]:
    """Runs that have an event log, newest first, with a one-line summary."""
    out = []
    for d in run_dirs(RUNS_ROOT):
        events = list(read_events(d))
        started = next((e for e in events if e["type"] == "run_started"), None)
        finished = any(e["type"] == "run_finished" for e in events)
        out.append({
            "run_id": d.name,
            "events": len(events),
            "running": not finished,
            "model": (started or {}).get("payload", {}).get("model"),
            "gates": (started or {}).get("payload", {}).get("gates"),
            "shipped": sum(1 for e in events if e["type"] == "shipped"),
            "failed": sum(1 for e in events if e["type"] == "task_failed"),
            "invalid": (d / "INVALID.md").is_file(),
        })
    return out


@app.get("/api/runs/{run_id}/events")
def get_events(run_id: str, after_seq: int = 0) -> list[dict]:
    """The log, or the part of it a reconnecting client is missing."""
    return list(read_events(_run_dir(run_id), after_seq=after_seq))


@app.get("/api/runs/{run_id}/artifacts/{path:path}")
def get_artifact(run_id: str, path: str) -> PlainTextResponse:
    """Fetch one artifact a payload pointed at (FR-28).

    Events carry pointers, not blobs, so this is where the diff or the test
    output is actually read -- lazily, only when someone clicks.
    """
    run = _run_dir(run_id)
    target = (run / path).resolve()
    if not target.is_relative_to(run) or not target.is_file():
        raise HTTPException(status_code=404, detail=f"no artifact {path!r}")
    return PlainTextResponse(target.read_text(encoding="utf-8", errors="replace"))


@app.websocket("/ws/live/{run_id}")
async def live(websocket: WebSocket, run_id: str, after_seq: int = 0) -> None:
    """Catch up from `after_seq`, then stream events as they land.

    The client sends its last seq on connect, so a dropped socket resumes
    exactly where it left off -- and a fresh client asking for 0 takes the same
    path. Reconnect and cold start are one code path (TAD §5.1).
    """
    await websocket.accept()
    try:
        run = (RUNS_ROOT / run_id).resolve()
        if not run.is_relative_to(RUNS_ROOT.resolve()) or not run.is_dir():
            await websocket.send_text(json.dumps({"error": f"no run {run_id!r}"}))
            await websocket.close()
            return
        async for event in tail_events(run, after_seq=after_seq):
            await websocket.send_text(json.dumps(event))
    except WebSocketDisconnect:
        pass  # a client going away is normal, not an error


@app.get("/", response_class=HTMLResponse)
def feed() -> str:
    """The text feed (FR-20). Week 4 puts the garage above this; it stays."""
    page = WEB_ROOT / "feed" / "index.html"
    if not page.is_file():
        return "<h1>web/feed/index.html is missing</h1>"
    return page.read_text(encoding="utf-8")
