"""The frontend's translation layer, exercised from the one test suite.

web/garage/source.js is the seam between the engine and the garage: it turns
backend events into the shape the scene consumes. It had no coverage at all --
it was checked by looking at a browser, which is exactly the kind of "verified"
that stops being true the moment someone edits it.

These run the real file under node against the real event log. Skipped, not
failed, where node is unavailable: the Python engine must never depend on a
JavaScript runtime being present.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
SOURCE = REPO / "web" / "garage" / "source.js"

pytestmark = pytest.mark.skipif(shutil.which("node") is None,
                                reason="node not installed")


def run_js(body: str) -> dict:
    """Execute a snippet with source.js imported, and return what it prints."""
    script = f'import {{ normalize, MockSource, LiveSource }} from "{SOURCE.as_uri()}";\n{body}'
    proc = subprocess.run(["node", "--input-type=module", "-e", script],
                          capture_output=True, text=True, timeout=60, check=False)
    assert proc.returncode == 0, proc.stderr[-1500:]
    return json.loads(proc.stdout.strip().splitlines()[-1])


def test_backend_events_translate_to_the_scene_shape():
    out = run_js("""
      const e = { type:"agent_activated", agent:"builder", task_id:"t1",
                  ts:"2026-08-19T21:15:37.000Z", payload:{attempt:2} };
      const [s] = normalize(e);
      console.log(JSON.stringify(s));
    """)
    assert out["worker"] == "kalia" and out["action"] == "build"
    assert out["status"] == "start" and out["job"] == "t1"
    assert isinstance(out["ts"], int)
    assert out["raw"]["payload"]["attempt"] == 2   # the panel still gets the payload


def test_every_role_id_maps_to_a_named_mechanic():
    out = run_js("""
      const roles = ["orchestrator","scout","builder","tester","reviewer","scribe"];
      const got = {};
      for (const r of roles) {
        const [s] = normalize({type:"agent_activated", agent:r, task_id:"t", ts:"2026-01-01T00:00:00Z"});
        got[r] = s ? s.worker : null;
      }
      console.log(JSON.stringify(got));
    """)
    assert out == {"orchestrator": "dholu", "scout": "bheem", "builder": "kalia",
                   "tester": "raju", "reviewer": "chutki", "scribe": "bholu"}


def test_a_verdict_becomes_a_result_the_tape_can_notch():
    out = run_js("""
      const [s] = normalize({type:"gate_verdict", agent:"reviewer", task_id:"t",
        ts:"2026-01-01T00:00:00Z", payload:{gate:"review", verdict:"reject"}});
      console.log(JSON.stringify(s));
    """)
    assert out["result"] == "reject" and out["worker"] == "chutki"


def test_unknown_event_types_are_ignored_not_fatal():
    """The schema is forward-compatible (ADR-10); a garage that threw on a new
    event type would make the log un-extendable."""
    out = run_js("""
      console.log(JSON.stringify({
        n: normalize({type:"something_invented_later", agent:"scout", ts:"2026-01-01T00:00:00Z"}).length
      }));
    """)
    assert out["n"] == 0


def test_the_live_source_translates_what_arrives_on_the_socket():
    """LiveSource had never been exercised outside a browser."""
    out = run_js("""
      let sent = null;
      globalThis.WebSocket = class {
        constructor(url) { sent = url; setTimeout(() => {
          this.onopen && this.onopen();
          this.onmessage({ data: JSON.stringify({type:"agent_activated",
            agent:"tester", task_id:"t9", ts:"2026-01-01T00:00:00Z", payload:{}}) });
        }, 0); }
        close() {}
      };
      globalThis.location = { protocol: "http:", host: "127.0.0.1:8899" };
      const got = [];
      new LiveSource("run42", e => got.push(e)).start();
      setTimeout(() => console.log(JSON.stringify({
        url: sent, events: got.map(e => e.meta || `${e.worker}:${e.status}`)
      })), 20);
    """)
    assert "/ws/live/run42?after_seq=0" in out["url"]
    assert out["events"] == ["connected", "raju:start"]


def test_the_real_event_log_survives_translation():
    log = REPO / "runs" / "watch" / "events.jsonl"
    if not log.is_file():
        pytest.skip("no recorded run on this machine")
    out = run_js(f"""
      import {{ readFileSync }} from "fs";
      const raw = readFileSync({json.dumps(str(log))}, "utf8").trim()
        .split("\\n").map(JSON.parse);
      const scene = raw.flatMap(normalize);
      console.log(JSON.stringify({{
        malformed: scene.filter(e => !e.status).length,
        workers: [...new Set(scene.filter(e => e.worker).map(e => e.worker))].sort(),
      }}));
    """)
    assert out["malformed"] == 0
    assert set(out["workers"]) <= {"dholu", "bheem", "kalia", "raju", "chutki", "bholu"}
