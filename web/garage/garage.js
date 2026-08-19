/* The garage scene (DESIGN.md §4, FR-21..FR-25).
 *
 * A PURE VIEW of the event log. Nothing here queries the engine; there is no
 * channel to query it on. Every sprite position is derived from events that
 * were written to disk, which is what makes the promise "every animation
 * corresponds to a real backend event" structurally true rather than a claim.
 *
 * Art is drawn programmatically rather than loaded from a sprite sheet. Kenney
 * CC0 sheets are the eventual source (DESIGN §8); until they are wired in there
 * is nothing to attribute and nothing to license.
 */
const T = {                                   // DESIGN §2.1
  ink0:0x12101A, ink1:0x1C1930, ink2:0x2A2545, ink3:0x3D3763,
  paper:0xE8E4D8, paperDim:0x9B96A8, work:0xFFB13D,
  pass:0x3DDC97, fail:0xFF5D5D, wire:0x5BC8F5,
};
const W = 960, H = 540, TILE = 32;            // §4.1 logical stage
const WALK_SPEED = 90;                        // px/s  §4.3
const ARC_MS = 400, STAMP_MS = 600, MICRO_MS = 120;
const REDUCED = window.matchMedia("(prefers-reduced-motion: reduce)").matches;

/* Stations. Each is a spot to stand plus the prop that identifies it in
 * silhouette (§4.2) -- the whole point is that you can tell who is working
 * without reading a label. */
const STATIONS = {
  orchestrator: { x:118, y:196, label:"WHITEBOARD" },
  scout:        { x:790, y:196, label:"FILING" },
  builder:      { x:330, y:330, label:"BUILD DESK" },
  tester:       { x:672, y:322, label:"TEST BENCH" },
  reviewer:     { x:672, y:412, label:"WORKBENCH" },
  scribe:       { x:672, y:486, label:"SIDE DESK" },
};
const COUCH = { x:150, y:452 };
/* Idle agents sit on the couch in two rows of three. At 14px apart their name
 * tags -- which DESIGN §4.3 requires always-on, since nobody can guess who is
 * who -- overlapped into an unreadable smear. */
const couchSeat = i => ({ x: 98 + (i % 3) * 64, y: 434 + Math.floor(i / 3) * 32 });
const AGENTS = ["orchestrator","scout","builder","tester","reviewer","scribe"];
const SKIN = { orchestrator:0x8FB7E8, scout:0xE8C07D, builder:0xB79CE8,
               tester:0x7DE8C0, reviewer:0xE89CB7, scribe:0xC0C0A8 };

const app = new PIXI.Application();
let layers = {}, avatars = {}, lamps = {}, monitors = null, needle = null;
let neon = null, dimmer = null, tally = null, chalk = null;
const flying = [];   // in-flight handoff papers
let shake = 0, freezeUntil = 0;

/* ------------------------------------------------------------------ scene */

function pixelRect(g, x, y, w, h, color, alpha = 1) {
  g.rect(Math.round(x), Math.round(y), Math.round(w), Math.round(h)).fill({ color, alpha });
}

function buildFloor() {
  const g = new PIXI.Graphics();
  pixelRect(g, 0, 0, W, H, T.ink0);                       // void
  pixelRect(g, 0, 0, W, 150, T.ink2);                     // back wall
  pixelRect(g, 0, 150, W, H - 150, T.ink1);               // floor
  // garage door: slats, closed
  pixelRect(g, 40, 22, 300, 118, T.ink3);
  for (let y = 30; y < 138; y += 12) pixelRect(g, 44, y, 292, 6, T.ink2);
  pixelRect(g, 168, 104, 44, 12, T.ink1);                 // mail slot -- ships fly out here
  // floor tape + cables (§4.2 ambient props)
  for (let x = 20; x < W - 20; x += 26) pixelRect(g, x, H - 26, 14, 3, T.ink3, .55);
  for (let x = 0; x < W; x += TILE) pixelRect(g, x, 150, 1, H - 150, T.ink2, .35);
  return g;
}

function label(text, x, y, size = 8, color = T.paperDim) {
  const t = new PIXI.Text({ text, style: {
    fontFamily: "Silkscreen, monospace", fontSize: size, fill: color, letterSpacing: 1 } });
  t.x = Math.round(x - t.width / 2); t.y = Math.round(y);
  return t;
}

function buildStations(root) {
  const g = new PIXI.Graphics();

  // whiteboard -- carries the loop diagram in chalk, and the retry tally
  pixelRect(g, 56, 168, 124, 78, T.paper, .93);
  pixelRect(g, 56, 168, 124, 78, T.ink3, .0);
  // filing boxes + hanging flashlight
  pixelRect(g, 748, 176, 44, 34, 0x6E5B3E); pixelRect(g, 796, 186, 40, 24, 0x5C4B33);
  pixelRect(g, 764, 156, 4, 20, T.ink3);
  // build desk: three monitors, the only lit screens in the room
  pixelRect(g, 262, 296, 140, 8, T.ink3);
  pixelRect(g, 268, 260, 40, 32, T.ink0); pixelRect(g, 312, 258, 40, 34, T.ink0);
  pixelRect(g, 356, 260, 40, 32, T.ink0);
  // test bench + oscilloscope
  pixelRect(g, 612, 300, 120, 8, T.ink3);
  pixelRect(g, 620, 268, 44, 32, T.ink0);
  // workbench: pegboard + the ladder (the ponytail pun, §4.2)
  pixelRect(g, 612, 392, 120, 6, T.ink3);
  for (let y = 356; y < 392; y += 9) pixelRect(g, 742, y, 26, 3, T.ink3);
  // side desk: typewriter
  pixelRect(g, 620, 470, 96, 6, T.ink3); pixelRect(g, 640, 456, 30, 14, T.ink2);
  // couch + pizza box
  pixelRect(g, 66, 424, 196, 62, T.ink2); pixelRect(g, 66, 412, 196, 14, T.ink3);
  pixelRect(g, 272, 462, 26, 16, 0x8A6B4A);   // pizza box
  // bin, for crumpled failures
  pixelRect(g, 268, 430, 22, 28, T.ink3, .8);
  root.addChild(g);

  for (const [name, s] of Object.entries(STATIONS)) {
    root.addChild(label(s.label, s.x, s.y + 26, 8));
    const lamp = new PIXI.Graphics();          // §2.2: only ONE glows at a time
    pixelRect(lamp, s.x - 16, s.y - 44, 32, 4, T.work);
    lamp.alpha = 0;
    root.addChild(lamp);
    lamps[name] = lamp;
  }

  monitors = new PIXI.Graphics();
  root.addChild(monitors);
  needle = new PIXI.Graphics();
  root.addChild(needle);

  // neon sign -- the one permitted gradient lives here (§2.2)
  neon = new PIXI.Text({ text: "THE GARAGE", style: {
    fontFamily: "Silkscreen, monospace", fontSize: 22, fill: T.work } });
  neon.x = 660; neon.y = 52; neon.alpha = .9;
  root.addChild(neon);

  chalk = new PIXI.Text({ text: "", style: {
    fontFamily: "IBM Plex Mono, monospace", fontSize: 9, fill: T.ink1,
    wordWrap: true, wordWrapWidth: 112 } });
  chalk.x = 62; chalk.y = 174;
  root.addChild(chalk);

  tally = new PIXI.Text({ text: "", style: {
    fontFamily: "Silkscreen, monospace", fontSize: 11, fill: T.ink1 } });
  tally.x = 62; tally.y = 226;
  root.addChild(tally);
}

function makeAvatar(name) {
  const c = new PIXI.Container();
  const g = new PIXI.Graphics();
  pixelRect(g, -6, -26, 12, 14, SKIN[name]);      // head
  pixelRect(g, -8, -12, 16, 14, T.ink3);          // body
  pixelRect(g, -7, 2, 5, 8, T.ink3);              // legs
  pixelRect(g, 2, 2, 5, 8, T.ink3);
  // one distinguishing prop each (§4.3)
  if (name === "scout")   pixelRect(g, -7, -28, 14, 3, T.work);        // headlamp
  if (name === "builder") pixelRect(g, -7, -28, 14, 5, 0x6E5BA8);      // hood
  if (name === "tester")  pixelRect(g, -7, -22, 14, 3, T.wire);        // safety glasses
  if (name === "reviewer")pixelRect(g, -9, -12, 3, 8, SKIN[name]);     // rolled sleeve
  if (name === "scribe")  pixelRect(g, -8, -13, 16, 4, 0xC96A6A);      // scarf
  if (name === "orchestrator") pixelRect(g, 5, -26, 3, 8, T.work);     // marker
  c.addChild(g);
  // Short tags: six full names at 8px collided even at 58px spacing. DESIGN
  // §4.3 wants them always visible, so shrink the text rather than hide it.
  const tag = label(name.slice(0, 6), 0, 8, 7, T.paperDim);
  c.addChild(tag);
  const glow = new PIXI.Graphics();
  pixelRect(glow, -12, 8, 24, 4, T.work, .5);
  glow.alpha = 0;
  c.addChildAt(glow, 0);
  c.glow = glow;
  return c;
}

/* --------------------------------------------------------------- motion */

function walkTo(name, x, y) {
  const a = avatars[name];
  a.target = { x, y };
  if (REDUCED) { a.x = x; a.y = y; }             // §6: walks become instant
}

function throwPaper(fromKey, toKey, color = T.paper) {
  if (REDUCED) return;
  const from = fromKey === "couch" ? COUCH : STATIONS[fromKey];
  const to   = toKey === "couch" ? COUCH : STATIONS[toKey];
  if (!from || !to) return;
  const g = new PIXI.Graphics();
  pixelRect(g, -4, -3, 8, 6, color);
  g.x = from.x; g.y = from.y - 20;
  layers.fx.addChild(g);
  flying.push({ g, t: 0, from: { x: from.x, y: from.y - 20 }, to: { x: to.x, y: to.y - 20 } });
}

/* The signature element (§4.5). The one screen shake and the one time-freeze
 * in the entire project -- spent here because the eval gate is the project's
 * whole identity, so a verdict cannot be a toast notification. */
function slamStamp(text, ok) {
  const wrap = new PIXI.Container();
  wrap.x = STATIONS.tester.x; wrap.y = STATIONS.tester.y - 40;
  const t = new PIXI.Text({ text, style: {
    fontFamily: "Silkscreen, monospace", fontSize: 48,
    fill: ok ? T.pass : T.fail } });
  t.anchor.set(.5); t.rotation = -6 * Math.PI / 180;
  wrap.addChild(t);
  wrap.scale.set(REDUCED ? 1 : 2.2);
  wrap.alpha = REDUCED ? 1 : 0;
  layers.fx.addChild(wrap);
  wrap.stampT = 0;
  wrap.isStamp = true;
  if (!REDUCED) { freezeUntil = performance.now() + MICRO_MS; shake = 2; }
  setTimeout(() => wrap.destroy(), STAMP_MS + 500);
}

function setWorkLamp(name) {                     // §2.2: singular --work glow
  for (const [k, lamp] of Object.entries(lamps)) lamp.alpha = k === name ? 1 : 0;
  for (const [k, a] of Object.entries(avatars)) a.glow.alpha = k === name ? 1 : 0;
}

/* -------------------------------------------------------------- reducer */

const S = { task:"—", attempt:"—", tests:[], review:[], spend:0,
            shipped:0, failed:0, retries:0, active:null };

function applyToScene(e) {
  const p = e.payload || {};
  switch (e.type) {
    case "task_started":
      S.task = e.task_id; S.attempt = "—"; S.tests = []; S.review = []; S.retries = 0;
      chalk.text = (e.task_id || "").replace(/__/g, " ");
      tally.text = "";
      if (!REDUCED) { dimmer.alpha = .1; setTimeout(() => dimmer.alpha = 0, MICRO_MS); }
      break;
    case "agent_activated": {
      const s = STATIONS[e.agent];
      if (s) { walkTo(e.agent, s.x, s.y); setWorkLamp(e.agent); S.active = e.agent; }
      if (p.attempt) S.attempt = p.attempt;
      break;
    }
    case "agent_done": {
      const seat = couchSeat(AGENTS.indexOf(e.agent));
      walkTo(e.agent, seat.x, seat.y);
      break;
    }
    case "handoff":       throwPaper(e.agent, p.to); break;
    case "context_pack_ready": throwPaper("scout", "builder"); break;
    case "patch_produced":     throwPaper("builder", "tester"); break;
    case "patch_apply_error":  throwPaper("builder", "builder", T.fail); break;
    case "tests_run":     needle.sweepUntil = performance.now() + 600; break;
    case "gate_verdict": {
      const ok = p.verdict === "pass" || p.verdict === "accept";
      (p.gate === "tests" ? S.tests : S.review).push(p.verdict);
      slamStamp(ok ? "PASS" : (p.gate === "review" ? "REJECT" : "FAIL"), ok);
      if (!ok) throwPaper("tester", "builder", T.fail);
      break;
    }
    case "retry":
      S.retries++;
      tally.text = "|".repeat(S.retries).replace(/(\|{5})/g, "$1 ");
      break;
    case "shipped":
      S.shipped++; throwPaper("tester", "orchestrator", T.pass);
      neon.tint = T.pass; setTimeout(() => neon.tint = 0xFFFFFF, 500);
      break;
    case "task_failed":
      S.failed++; chalk.text += "\n✗ " + (p.reason || "failed");
      break;
    case "budget_exceeded":
      dimmer.alpha = .6; break;                  // the lamp physically dims (§4.4)
    case "cost_tick":
      if (typeof p.usd === "number") S.spend = p.usd; break;
    // unknown types: ignored on purpose (schema v:1 forward compatibility)
  }
}

/* ---------------------------------------------------------------- ticker */

function tick(ticker) {
  const now = performance.now();
  const dt = ticker.deltaMS / 1000;
  if (now < freezeUntil) return;                 // the world waits for the gate

  for (const [name, a] of Object.entries(avatars)) {
    if (!a.target) continue;
    const dx = a.target.x - a.x, dy = a.target.y - a.y;
    const d = Math.hypot(dx, dy);
    if (d < 1.5) { a.x = a.target.x; a.y = a.target.y; a.target = null; continue; }
    const step = Math.min(d, WALK_SPEED * dt);
    a.x = Math.round(a.x + (dx / d) * step);
    a.y = Math.round(a.y + (dy / d) * step);
  }

  for (let i = flying.length - 1; i >= 0; i--) {
    const f = flying[i];
    f.t += ticker.deltaMS / ARC_MS;
    if (f.t >= 1) { f.g.destroy(); flying.splice(i, 1); continue; }
    const k = f.t;
    f.g.x = Math.round(f.from.x + (f.to.x - f.from.x) * k);
    f.g.y = Math.round(f.from.y + (f.to.y - f.from.y) * k - Math.sin(k * Math.PI) * 46);
  }

  for (const c of layers.fx.children) {
    if (!c.isStamp) continue;
    c.stampT += ticker.deltaMS / STAMP_MS;
    const k = Math.min(1, c.stampT * 3);
    c.alpha = Math.min(1, c.stampT * 6);
    c.scale.set(2.2 - 1.2 * (k * k));            // ease-in: it is gravity (§6)
  }

  monitors.clear();
  const lit = S.active === "builder";
  for (let i = 0; i < 3; i++)
    pixelRect(monitors, 268 + i * 44, i === 1 ? 258 : 260, 40, i === 1 ? 34 : 32,
              lit ? T.wire : T.ink0, lit ? .25 + .1 * Math.sin(now / 220 + i) : 1);

  needle.clear();
  if (needle.sweepUntil && now < needle.sweepUntil) {
    const a = -Math.PI * .75 + Math.PI * .5 * (1 + Math.sin(now / 90));
    needle.moveTo(642, 292).lineTo(642 + Math.cos(a) * 18, 292 + Math.sin(a) * 18)
          .stroke({ width: 2, color: T.work });
  }

  if (shake > 0) {
    layers.root.x = Math.round((Math.random() - .5) * shake * 2);
    layers.root.y = Math.round((Math.random() - .5) * shake * 2);
    shake -= dt * 12;
    if (shake <= 0) { shake = 0; layers.root.x = layers.root.y = 0; }
  }
}

/* -------------------------------------------------------------- plumbing */

const $ = id => document.getElementById(id);
const stampGlyph = v => v === "pass" || v === "accept"
  ? '<span class="ok">✓</span>' : v === "fail" || v === "reject"
  ? '<span class="no">✗</span>' : "·";

function paintHud() {
  $("task").textContent = S.task;
  $("attempt").textContent = S.attempt;
  $("gtests").innerHTML = S.tests.map(stampGlyph).join("") || "·";
  $("greview").innerHTML = S.review.map(stampGlyph).join("") || "·";
  $("spend").textContent = "$" + S.spend.toFixed(4);
  $("shipped").textContent = S.shipped;
  $("failed").textContent = S.failed;
}

function feedRow(e) {
  const p = e.payload || {};
  const v = p.verdict;
  const cls = (e.type === "shipped" || v === "pass" || v === "accept") ? "pass"
            : (e.type === "task_failed" || e.type === "patch_apply_error"
               || v === "fail" || v === "reject") ? "fail" : "";
  const kv = Object.entries(p).filter(([k]) => k !== "artifact")
    .map(([k, x]) => `${k}=${typeof x === "object" ? JSON.stringify(x) : x}`).join(" ");
  const row = document.createElement("div");
  row.className = "row " + cls;
  row.innerHTML = `<span class="t">${(e.ts||"").slice(11,19)}</span>` +
    `<span class="ag">${e.agent}</span><span class="ty">${e.type}</span>` +
    `<span class="kv">${kv.replace(/[<>&]/g, c => ({'<':'&lt;','>':'&gt;','&':'&amp;'}[c]))}</span>`;
  const f = $("feed");
  const stuck = f.scrollTop + f.clientHeight >= f.scrollHeight - 30;
  f.appendChild(row);
  if (stuck) f.scrollTop = f.scrollHeight;
}

let ws = null;
function openRun(id) {
  if (ws) { ws.close(); ws = null; }
  $("feed").innerHTML = "";
  Object.assign(S, { task:"—", attempt:"—", tests:[], review:[], spend:0,
                     shipped:0, failed:0, retries:0, active:null });
  chalk.text = ""; tally.text = ""; dimmer.alpha = 0;
  setWorkLamp(null);
  AGENTS.forEach((n, i) => { const st = couchSeat(i); walkTo(n, st.x, st.y); });
  paintHud();
  $("status").textContent = "connecting…"; $("dot").className = "dot";

  const proto = location.protocol === "https:" ? "wss" : "ws";
  ws = new WebSocket(`${proto}://${location.host}/ws/live/${id}?after_seq=0`);
  ws.onopen = () => { $("status").textContent = "live"; $("dot").className = "dot live"; };
  ws.onmessage = ev => {
    const e = JSON.parse(ev.data);
    if (e.error) { $("status").textContent = e.error; return; }
    applyToScene(e); feedRow(e); paintHud();
    if (e.type === "run_finished") $("status").textContent = "run finished";
  };
  ws.onclose = () => $("dot").classList.remove("live");
}

async function boot() {
  await app.init({ width: W, height: H, background: T.ink0,
                   antialias: false, roundPixels: true, autoDensity: false });
  $("stage").appendChild(app.canvas);

  layers.root = new PIXI.Container();
  app.stage.addChild(layers.root);
  layers.root.addChild(buildFloor());
  buildStations(layers.root);
  layers.actors = new PIXI.Container(); layers.root.addChild(layers.actors);
  layers.fx = new PIXI.Container();     layers.root.addChild(layers.fx);

  dimmer = new PIXI.Graphics();
  pixelRect(dimmer, 0, 0, W, H, T.ink0);
  dimmer.alpha = 0;
  layers.root.addChild(dimmer);

  AGENTS.forEach((n, i) => {
    const a = makeAvatar(n);
    const seat = couchSeat(i); a.x = seat.x; a.y = seat.y;
    layers.actors.addChild(a); avatars[n] = a;
  });

  const fit = () => {
    const box = $("stage").getBoundingClientRect();
    const raw = Math.min(box.width / W, box.height / H);
    // Integer steps keep pixel art crisp, but only when the stage actually
    // fits. Forcing a floor of 1 on a short viewport clipped the couch and the
    // side desk clean off the bottom.
    const s = raw >= 1 ? Math.floor(raw * 2) / 2 : raw;
    app.canvas.style.width = `${W * s}px`; app.canvas.style.height = `${H * s}px`;
  };
  fit(); addEventListener("resize", fit);
  app.ticker.add(tick);

  const runs = await (await fetch("/api/runs")).json();
  const sel = $("runs");
  runs.forEach(r => {
    const o = document.createElement("option");
    o.value = r.run_id;
    o.textContent = `${r.run_id} · ${r.events} events` +
      (r.running ? " · running" : "") + (r.invalid ? " · INVALID" : "");
    sel.appendChild(o);
  });
  sel.onchange = () => openRun(sel.value);
  if (runs.length) openRun(runs[0].run_id);
  else $("status").textContent = "no runs yet";

  // feed-only mode: the demo must work with the garage off (DESIGN §5.1)
  $("toggleFeed").onclick = () => {
    const on = document.body.style.gridTemplateRows !== "48px 0px 1fr";
    document.body.style.gridTemplateRows = on ? "48px 0px 1fr" : "48px 1fr 190px";
    $("toggleFeed").textContent = on ? "show garage" : "feed only";
  };
}
boot();
