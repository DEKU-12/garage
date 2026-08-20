/* Where the garage gets its events (the seam).
 *
 * The scene knows ONE event shape and nothing about where it came from:
 *
 *     { worker, action, job, status, result, ts, raw }
 *
 *       worker  dholu | bheem | kalia | raju | chutki | bholu
 *       action  plan | find | build | test | review | record | ship | fail
 *       job     what is on the lift right now (a file, a task id)
 *       status  start | done | job_start | job_end
 *       result  null | pass | fail | reject | accept | unverified
 *       raw     the untranslated backend event, when there was one
 *
 * Two sources implement it. `MockSource` invents a plausible night out of
 * nothing; `LiveSource` opens the real WebSocket and translates. Swapping one
 * for the other is a one-line change in boot() and touches no drawing code --
 * which is the whole point of this file existing separately.
 *
 * `raw` is carried but never read by the scene. It is there so the artifact
 * panel can still fetch what an agent actually wrote, without the scene
 * needing to know that backend events have payloads at all.
 */

/* The engine speaks role ids and event types. The garage speaks names and
 * verbs. This table is the entire translation, and it is the only place that
 * knows both vocabularies. */
const WORKER_OF = {
  orchestrator: "dholu", scout: "bheem", builder: "kalia",
  tester: "raju", reviewer: "chutki", scribe: "bholu",
};
const ACTION_OF = {
  dholu: "plan", bheem: "find", kalia: "build",
  raju: "test", chutki: "review", bholu: "record",
};

function ev(worker, status, extra = {}) {
  return {
    worker, status,
    action: extra.action ?? (worker ? ACTION_OF[worker] : null),
    job: extra.job ?? null,
    result: extra.result ?? null,
    // Why a stage ended, when it ended badly: patch_rejected, apply_failed,
    // model_error, budget_exceeded, grading_infra_error, reviewer_unavailable.
    outcome: extra.outcome ?? "",
    ts: extra.ts ?? Date.now(),
    raw: extra.raw ?? null,
  };
}

/* ------------------------------------------------------------------ live */

/* Translate one backend event into zero or more scene events. Returning an
 * array matters: `gate_verdict` is both "a result happened" and "this worker
 * is finished", and the scene should not have to infer the second from the
 * first. */
export function normalize(e) {
  const p = e.payload || {};
  const worker = WORKER_OF[e.agent] || null;
  const at = { ts: Date.parse(e.ts || 0) || Date.now(), raw: e, job: e.task_id };

  switch (e.type) {
    case "task_started":
      return [ev(null, "job_start", { ...at, action: null, job: e.task_id })];
    case "agent_activated":
      return worker ? [ev(worker, "start", at)] : [];
    case "agent_done":
      // Carry WHY they stopped. Three builder attempts in a row all read
      // "build done" otherwise, when two of them produced an unusable patch --
      // a feed that renders failure identically to success is the one thing
      // this project cannot afford.
      return worker ? [ev(worker, "done", { ...at, outcome: p.outcome || "" })] : [];
    case "gate_verdict":
      return worker ? [ev(worker, "result", { ...at, result: p.verdict })] : [];
    case "shipped":
      return [ev(null, "job_end", { ...at, action: "ship", result: "pass" })];
    case "task_failed":
      return [ev(null, "job_end", { ...at, action: "fail", result: "fail" })];
    default:
      // Unknown types are ignored on purpose: the schema is forward-compatible
      // (ADR-10), and a garage that throws on a new event type would make the
      // log un-extendable.
      return [];
  }
}

export class LiveSource {
  constructor(runId, onEvent) {
    this.runId = runId; this.onEvent = onEvent; this.ws = null;
  }
  start() {
    const proto = location.protocol === "https:" ? "wss" : "ws";
    this.ws = new WebSocket(
      `${proto}://${location.host}/ws/live/${this.runId}?after_seq=0`);
    this.ws.onmessage = m => {
      const e = JSON.parse(m.data);
      if (e.error) return this.onEvent({ error: e.error });
      for (const scene of normalize(e)) this.onEvent(scene);
    };
    this.ws.onopen = () => this.onEvent({ meta: "connected" });
    this.ws.onclose = () => this.onEvent({ meta: "closed" });
  }
  stop() { if (this.ws) { this.ws.close(); this.ws = null; } }
}

/* ------------------------------------------------------------------ mock */

/* A believable night, invented locally. Exists so the garage can be built,
 * demoed and looked at with no engine, no Docker and no network -- and so the
 * shape above is exercised by something other than the real thing, which is
 * how you find out the shape is wrong. */
const JOBS = [
  "auth/validators.py", "core/serializers.py", "utils/dates.py",
];

export class MockSource {
  constructor(onEvent, { speed = 1 } = {}) {
    this.onEvent = onEvent; this.speed = speed; this.timers = []; this.stopped = false;
  }

  /* One job, told as a list of [delay, event] with the retry the real engine
   * would produce: the builder gets rejected once and goes round again. */
  script(job) {
    const beat = 1500 / this.speed;
    const seq = [];
    let t = 0;
    const at = (ms, e) => { t += ms; seq.push([t, e]); };

    at(beat * 0.5, ev(null, "job_start", { job, action: null }));
    // The foreman decides who goes next before every stage -- that is what the
    // engine's routing emits, so the mock does it too rather than showing a
    // tidier garage than the real one.
    for (const w of ["dholu", "bheem", "kalia"]) {
      at(beat, ev(w, "start", { job }));
      at(beat * 2, ev(w, "done", { job }));
    }
    at(beat * 0.6, ev("raju", "start", { job }));
    at(beat * 2.2, ev("raju", "result", { job, result: "pass" }));
    at(beat * 0.4, ev("raju", "done", { job }));

    at(beat * 0.6, ev("chutki", "start", { job }));
    at(beat * 2, ev("chutki", "result", { job, result: "reject" }));
    at(beat * 0.4, ev("chutki", "done", { job }));

    // the rejection sends it back to the builder -- the loop, made visible
    at(beat * 0.6, ev("kalia", "start", { job }));
    at(beat * 2, ev("kalia", "done", { job }));
    at(beat * 0.5, ev("raju", "start", { job }));
    at(beat * 2, ev("raju", "result", { job, result: "pass" }));
    at(beat * 0.3, ev("raju", "done", { job }));
    at(beat * 0.5, ev("chutki", "start", { job }));
    at(beat * 1.6, ev("chutki", "result", { job, result: "accept" }));
    at(beat * 0.3, ev("chutki", "done", { job }));

    at(beat * 0.5, ev("bholu", "start", { job }));
    at(beat * 1.4, ev("bholu", "done", { job }));
    at(beat * 0.6, ev(null, "job_end", { job, action: "ship", result: "pass" }));
    return { seq, total: t + beat };
  }

  start() {
    this.stopped = false;
    let jobIndex = 0;
    const runJob = () => {
      if (this.stopped) return;
      const { seq, total } = this.script(JOBS[jobIndex % JOBS.length]);
      jobIndex++;
      for (const [delay, e] of seq) {
        this.timers.push(setTimeout(() => {
          if (!this.stopped) this.onEvent({ ...e, ts: Date.now() });
        }, delay));
      }
      this.timers.push(setTimeout(runJob, total));   // and the next car rolls in
    };
    this.onEvent({ meta: "mock" });
    runJob();
  }

  stop() {
    this.stopped = true;
    this.timers.forEach(clearTimeout);
    this.timers = [];
  }
}
