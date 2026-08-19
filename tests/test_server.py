"""Event server — routing, sandboxing, and the reconnect path (FR-19).

Offline: exercises the app against fixture run directories, no network.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from engine.events import EventLog


@pytest.fixture
def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    import server.app as app_mod

    runs = tmp_path / "runs"
    (runs / "r_one").mkdir(parents=True)
    log = EventLog.for_run(runs / "r_one", "r_one")
    log.emit("run_started", agent="orchestrator", model="stub")
    log.emit("task_started", agent="orchestrator", task_id="t-1")
    log.emit("gate_verdict", agent="tester", task_id="t-1",
             gate="tests", verdict="pass", attempt=1,
             artifact="tasks/t-1/attempts/1/test_output.txt")
    log.emit("shipped", agent="orchestrator", task_id="t-1")
    log.emit("run_finished", agent="orchestrator")

    art = runs / "r_one" / "tasks" / "t-1" / "attempts" / "1"
    art.mkdir(parents=True)
    (art / "test_output.txt").write_text("3 passed")

    monkeypatch.setattr(app_mod, "RUNS_ROOT", runs)
    return TestClient(app_mod.app)


def test_lists_runs_with_a_summary(client: TestClient) -> None:
    runs = client.get("/api/runs").json()
    assert [r["run_id"] for r in runs] == ["r_one"]
    assert runs[0]["shipped"] == 1 and runs[0]["running"] is False


def test_serves_the_whole_log(client: TestClient) -> None:
    events = client.get("/api/runs/r_one/events").json()
    assert [e["seq"] for e in events] == [1, 2, 3, 4, 5]


def test_after_seq_serves_only_the_gap(client: TestClient) -> None:
    """The reconnect path: a client says where it got to, gets the remainder."""
    events = client.get("/api/runs/r_one/events?after_seq=3").json()
    assert [e["seq"] for e in events] == [4, 5]


def test_a_missing_run_is_a_404_not_a_traceback(client: TestClient) -> None:
    """The server never crashes on a bad run dir (rules.md §3.1)."""
    assert client.get("/api/runs/nope/events").status_code == 404


def test_artifacts_are_fetched_lazily(client: TestClient) -> None:
    """Events carry pointers; this is where the blob is actually read."""
    r = client.get("/api/runs/r_one/artifacts/tasks/t-1/attempts/1/test_output.txt")
    assert r.status_code == 200 and r.text == "3 passed"


def test_artifact_paths_cannot_escape_the_run_directory(client: TestClient) -> None:
    """run_id and path both arrive from the URL and are untrusted."""
    assert client.get("/api/runs/r_one/artifacts/../../../etc/hosts").status_code == 404
    assert client.get("/api/runs/..%2F..%2Fetc/events").status_code == 404


def test_the_websocket_replays_a_finished_run_and_closes(client: TestClient) -> None:
    """after_seq=0 on a finished run IS replay -- same path as a live tail."""
    with client.websocket_connect("/ws/live/r_one?after_seq=0") as ws:
        seqs = [json.loads(ws.receive_text())["seq"] for _ in range(5)]
    assert seqs == [1, 2, 3, 4, 5]


def test_the_websocket_honours_after_seq(client: TestClient) -> None:
    with client.websocket_connect("/ws/live/r_one?after_seq=3") as ws:
        seqs = [json.loads(ws.receive_text())["seq"] for _ in range(2)]
    assert seqs == [4, 5]


def test_the_feed_page_is_served(client: TestClient) -> None:
    r = client.get("/")
    assert r.status_code == 200 and "The&nbsp;Garage" in r.text
