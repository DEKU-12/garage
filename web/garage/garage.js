import { MockSource, LiveSource } from "./source.js";

/* The garage scene (DESIGN.md §4, FR-21..FR-25).
 *
 * A PURE VIEW of the event log. Nothing here queries the engine; there is no
 * channel to query it on. Every sprite position is derived from events that
 * were written to disk, which is what makes the promise "every animation
 * corresponds to a real backend event" structurally true rather than a claim.
 *
 * Camera is a slight top-down overhead: the whole floor, the car's route and
 * every mechanic stay visible at once, which is what replay needs.
 *
 * Art is drawn programmatically rather than loaded from a sprite sheet. Kenney
 * CC0 sheets are the eventual source (DESIGN §8); until they are wired in there
 * is nothing to attribute and nothing to license.
 */
const T = {                                   // DESIGN §2.1 -- dark & cosy
  // the four that carry MEANING and appear nowhere decorative (§2.2)
  work:0xF0B24B, pass:0x46B46A, fail:0xD95A5A, wire:0x4D8FDB,
  // the room
  coal:0x211B19, walnut:0x4A382D, concrete:0x6D5B48, tan:0xB8A68B,
  parchment:0xE8DAB9, warm:0xD8C18A,
  // derived shades, all mixed from the six above -- no new hues invented
  coalLift:0x2C2422, walnutLift:0x5C4736, concreteDark:0x584938,
  concreteLift:0x7D6A54, tanDark:0x8E7F62, shadow:0x140F0E,
};
const W = 960, H = 540, TILE = 32;            // §4.1 logical stage
const WALK_SPEED = 300;                       // px/s  §4.3. The longest walk is
const CAR_SPEED = 210;                        // ~510px; replay fires events far
                                              // faster than a live run, and a walk
                                              // that cannot finish reads as nobody
                                              // ever leaving the couch.
const ARC_MS = 400, STAMP_MS = 600, MICRO_MS = 120;
const REDUCED = window.matchMedia("(prefers-reduced-motion: reduce)").matches;

/* Scrubbing means rebuilding the scene at an arbitrary point by folding the
 * log from the start (FR-27). That fold must not animate -- 400 handoff arcs
 * and 40 stamps firing at once would be both wrong and unwatchable -- so every
 * animated effect checks `quiet()` and snaps instead. */
let SILENT = false;
const quiet = () => SILENT || REDUCED;

/* Stations. Each is a spot to STAND, the prop that identifies it in silhouette
 * (§4.2), and the one word that pops over the mechanic's head while they work
 * -- the popup is what lets someone read the floor without reading the feed. */
const STATIONS = {
  orchestrator: { x:152, y:214, label:"WHITEBOARD",   word:"PLAN",   car:{ x:250, y:250 } },
  scout:        { x:392, y:214, label:"FILING BOXES", word:"FIND",   car:{ x:392, y:268 } },
  builder:      { x:660, y:222, label:"BUILD DESK",   word:"BUILD",  car:{ x:596, y:274 } },
  tester:       { x:672, y:452, label:"TEST BENCH",   word:"TEST",   car:{ x:600, y:398 } },
  reviewer:     { x:404, y:452, label:"WORKBENCH",    word:"REVIEW", car:{ x:404, y:392 } },
  scribe:       { x:118, y:352, label:"SIDE DESK",    word:"RECORD", car:{ x:236, y:352 } },
};
const COUCH   = { x:150, y:474 };
const CAR_HOME = { x:470, y:322 };            // the lift, dead centre
const DOOR     = { x:906, y:250 };            // ship out, through the right wall

/* Where a mechanic stands to work ON the car. The car never moves to them --
 * it is the job, parked on the lift, and they come to it. Each slot is on the
 * side the mechanic's own workstation is on, so nobody crosses the floor
 * diagonally and no two walking paths overlap. */
const BAY = {
  orchestrator: { x:360, y:288 },   // whiteboard is top-left
  scribe:       { x:360, y:356 },   // side desk is left
  scout:        { x:470, y:246 },   // filing boxes are top-middle
  builder:      { x:586, y:288 },   // build desk is top-right
  tester:       { x:586, y:356 },   // test bench is bottom-right
  reviewer:     { x:470, y:400 },   // workbench is bottom-middle
};

/* The garage speaks names; the scene's internals speak the engine's role ids,
 * because STATIONS, CHARS and the artifact panel all key off them. One map,
 * one direction. */
const ROLE_OF = { dholu:"orchestrator", bheem:"scout", kalia:"builder",
                  raju:"tester", chutki:"reviewer", bholu:"scribe" };
const roleOf = w => ROLE_OF[w] || w;

/* Idle mechanics sit by the couch in two rows of three. At 14px apart their
 * name tags -- which DESIGN §4.3 requires always-on, since nobody can guess
 * who is who -- overlapped into an unreadable smear. */
const couchSeat = i => ({ x: 84 + (i % 3) * 60, y: 446 + Math.floor(i / 3) * 46 });
const AGENTS = ["orchestrator","scout","builder","tester","reviewer","scribe"];

/* The crew's names. The engine's role ids (scout/builder/tester/...) are a
 * CONTRACT -- events.py validates every event against that frozen set and the
 * whole test suite speaks it -- so the renaming happens here in the view, the
 * one place it costs nothing and can break nothing. */
const DISPLAY = {
  orchestrator:"Dholu", scout:"Bheem",    builder:"Kalia",
  tester:"Raju",        reviewer:"Chutki", scribe:"Bholu",
};
const shown = k => DISPLAY[k] || k;

/* Plain-English job descriptions for the crew panel. Written for someone who
 * has never read the PRD -- the panel is the only place the scene explains
 * what the six mechanics are actually for. */
const ROLES = {
  orchestrator: "Hands out the job and decides who works next.",
  scout:        "Reads the repo and finds the files that actually matter.",
  builder:      "Writes the fix, as a patch.",
  tester:       "Runs the test suite in Docker and reports what broke.",
  reviewer:     "Judges the patch, and can send it back to be redone.",
  scribe:       "Keeps the record of what happened.",
};

/* Six bodies that must stay apart at 24x32. Silhouette does most of the work
 * (width/height/hair), colour only confirms it -- a palette swap alone stops
 * reading once two mechanics stand near the same lamp.
 *
 * NOTE ON SHIRT COLOURS: §2.2 reserves amber for "working now", so Kalia's
 * hoodie and Raju's tee are pulled down to rust and ochre. Next to the actual
 * --work amber they read as cloth, not as status. */
const CHARS = {
  orchestrator:{ role:"Foreman",  skin:0xC98D63, hair:0x241C18, shirt:0x3E6FA8, w:12, tall:28, prop:"clipboard" },
  scout:       { role:"Scout",    skin:0xD2A06B, hair:0x1B1512, shirt:0x4E7A4A, w:15, tall:30, prop:"none" },
  builder:     { role:"Builder",  skin:0xC98D63, hair:0x241C18, shirt:0xB0552A, w:19, tall:28, prop:"hood" },
  tester:      { role:"Tester",   skin:0xE0B183, hair:0x241C18, shirt:0xB8863C, w:12, tall:24, prop:"cap" },
  reviewer:    { role:"Reviewer", skin:0xE8BC90, hair:0x2A1F1A, shirt:0x6E5AA8, w:13, tall:27, prop:"ponytail" },
  scribe:      { role:"Scribe",   skin:0xC98D63, hair:0x241C18, shirt:0x47708C, w:12, tall:26, prop:"glasses" },
};

const app = new PIXI.Application();
let layers = {}, avatars = {}, lamps = {}, bubbles = {};
let monitors = null, needle = null, car = null;
let neon = null, dimmer = null, tally = null, chalk = null;
const flying = [];   // in-flight handoff papers
let shake = 0, freezeUntil = 0;

/* ------------------------------------------------------------------ scene */

function pixelRect(g, x, y, w, h, color, alpha = 1) {
  g.rect(Math.round(x), Math.round(y), Math.round(w), Math.round(h)).fill({ color, alpha });
}

/* A prop seen from slightly above: dark base, lit top face, coal outline. The
 * outline is what stops everything dissolving into one brown smear -- at this
 * palette's contrast, shape has to be carried by the edge, not the fill. */
function prop(g, x, y, w, h, face, top = null, lip = 5) {
  pixelRect(g, x - 1, y - 1, w + 2, h + 2, T.coal);
  pixelRect(g, x, y, w, h, face);
  if (top !== null) pixelRect(g, x, y, w, lip, top);
}

function label(text, x, y, size = 8, color = T.tan) {
  const t = new PIXI.Text({ text, style: {
    fontFamily: "Silkscreen, monospace", fontSize: size, fill: color, letterSpacing: 1 } });
  t.x = Math.round(x - t.width / 2); t.y = Math.round(y);
  return t;
}

function buildFloor() {
  const g = new PIXI.Graphics();
  pixelRect(g, 0, 0, W, H, T.coal);                        // void beyond the walls

  // concrete floor, grouted on the tile grid with a lifted checker so the
  // room has texture without any prop on it
  pixelRect(g, 24, 96, W - 48, H - 120, T.concrete);
  for (let y = 96; y < H - 24; y += TILE)
    for (let x = 24; x < W - 24; x += TILE)
      if (((x / TILE | 0) + (y / TILE | 0)) % 2)
        pixelRect(g, x, y, TILE, TILE, T.concreteLift, .22);
  for (let x = 24; x <= W - 24; x += TILE) pixelRect(g, x, 96, 1, H - 120, T.concreteDark, .5);
  for (let y = 96; y <= H - 24; y += TILE) pixelRect(g, 24, y, W - 48, 1, T.concreteDark, .5);

  // back wall + walnut beam along its foot
  pixelRect(g, 24, 24, W - 48, 72, T.coalLift);
  pixelRect(g, 24, 88, W - 48, 8, T.walnut);
  for (let x = 60; x < W - 60; x += 128) pixelRect(g, x, 24, 10, 64, T.walnut, .55);
  // side walls
  pixelRect(g, 0, 0, 24, H, T.coal); pixelRect(g, W - 24, 0, 24, H, T.coal);
  pixelRect(g, 0, 0, W, 24, T.coal); pixelRect(g, 0, H - 24, W, 24, T.coal);

  // the roller door, right wall -- finished work leaves through the mail slot
  prop(g, 856, 132, 78, 232, T.walnut, T.walnutLift, 0);
  for (let y = 140; y < 356; y += 14) pixelRect(g, 862, y, 66, 7, T.coalLift, .55);
  pixelRect(g, 872, 236, 46, 12, T.coal);                  // mail slot
  pixelRect(g, 874, 238, 42, 8, T.concreteDark);
  prop(g, 846, 100, 96, 20, T.parchment, null, 0);         // SHIP OUT sign

  // the lift bay the car parks on
  pixelRect(g, 388, 268, 168, 108, T.concreteDark, .75);
  for (let x = 388; x < 556; x += 16) pixelRect(g, x, 268, 8, 4, T.work, .5);
  for (let x = 388; x < 556; x += 16) pixelRect(g, x, 372, 8, 4, T.work, .5);

  // floor cables snaking out of the build desk
  for (let x = 600; x < 840; x += 22) pixelRect(g, x, 300, 12, 3, T.coal, .6);
  return g;
}

function buildStations(root) {
  const g = new PIXI.Graphics();

  // 1 whiteboard -- the plan loop, in chalk
  prop(g, 60, 118, 180, 92, T.parchment, null);
  pixelRect(g, 60, 118, 180, 7, T.walnut);
  const chalkC = 0x8C8375;
  [[86,152,34,3],[150,152,34,3],[86,192,34,3],[150,192,34,3]].forEach(([x,y,w,h]) =>
    pixelRect(g, x, y, w, h, chalkC));          // PLAN -> FIND / REVIEW <- TEST
  pixelRect(g, 128, 153, 18, 2, chalkC); pixelRect(g, 128, 193, 18, 2, chalkC);
  pixelRect(g, 100, 158, 2, 30, chalkC); pixelRect(g, 182, 158, 2, 30, chalkC);
  pixelRect(g, 118, 170, 44, 2, chalkC); pixelRect(g, 158, 166, 6, 10, chalkC);
  // 2 filing boxes + shelving
  prop(g, 316, 132, 54, 40, T.tanDark, T.tan);
  prop(g, 374, 146, 46, 34, T.tanDark, T.tan);
  prop(g, 322, 176, 44, 30, T.tanDark, T.tan);
  pixelRect(g, 300, 120, 6, 22, T.coal);                   // hanging flashlight cord
  pixelRect(g, 294, 142, 18, 10, T.tan);
  // 3 build desk -- three monitors, the only lit screens in the room
  prop(g, 566, 176, 196, 30, T.walnut, T.walnutLift);
  prop(g, 574, 128, 58, 44, T.coal, null);
  prop(g, 638, 122, 58, 50, T.coal, null);
  prop(g, 702, 128, 58, 44, T.coal, null);
  // 4 test bench + oscilloscope + the stamp block
  prop(g, 566, 384, 212, 34, T.walnut, T.walnutLift);
  prop(g, 576, 344, 66, 42, T.coal, null);                 // scope housing
  prop(g, 712, 348, 34, 38, T.tanDark, T.tan);             // the stamp, resting
  // 5 workbench: pegboard of tools + the ladder leaning beside it (§4.2 pun)
  prop(g, 300, 380, 208, 34, T.walnut, T.walnutLift);
  prop(g, 306, 316, 196, 62, T.walnutLift, null);
  for (let y = 326; y < 372; y += 12)
    for (let x = 316; x < 494; x += 18) pixelRect(g, x, y, 8, 6, T.tanDark, .85);
  for (let y = 320; y < 412; y += 16) pixelRect(g, 520, y, 30, 4, T.tan);   // ladder rungs
  pixelRect(g, 518, 316, 4, 100, T.tan); pixelRect(g, 546, 316, 4, 100, T.tan);
  // 6 side desk: typewriter + mug
  prop(g, 44, 322, 132, 30, T.walnut, T.walnutLift);
  prop(g, 66, 296, 46, 28, T.tanDark, T.tan);
  pixelRect(g, 128, 306, 12, 14, T.parchment);
  // couch + pizza box + rug
  pixelRect(g, 46, 424, 232, 92, T.walnut, .35);           // rug
  prop(g, 54, 430, 216, 62, 0x4C5A3C, 0x63734F);
  prop(g, 62, 418, 200, 16, 0x3E4A32, null, 0);
  prop(g, 214, 470, 40, 22, T.tan, T.parchment);           // pizza box
  // bin, for crumpled failures
  prop(g, 812, 434, 42, 46, T.tanDark, T.tan);
  for (let i = 0; i < 5; i++)                                   // crumpled paper
    pixelRect(g, 800 + (i * 17) % 62, 484 + (i % 2) * 9, 7, 6, T.parchment, .65);

  // wall furniture: pipes, clock, breaker box, SHIP OUT sign
  pixelRect(g, 24, 40, W - 48, 6, T.walnutLift, .5);
  for (let x = 120; x < W - 80; x += 96) pixelRect(g, x, 40, 8, 14, T.walnut);
  prop(g, 300, 46, 24, 24, T.tanDark, T.tan);                   // clock
  pixelRect(g, 310, 52, 2, 8, T.coal); pixelRect(g, 312, 58, 7, 2, T.coal);
  prop(g, 792, 44, 34, 30, T.walnut, T.walnutLift);             // breaker box
  pixelRect(g, 800, 52, 8, 6, T.pass, .8); pixelRect(g, 812, 52, 8, 6, T.work, .8);

  // potted plants, one each side of the couch
  [[36, 400], [286, 452]].forEach(([x, y]) => {
    prop(g, x, y + 18, 22, 18, T.tanDark, T.tan);
    pixelRect(g, x + 4, y, 14, 20, 0x4C6B3C);
    pixelRect(g, x + 1, y + 6, 8, 12, 0x3E5A31);
    pixelRect(g, x + 13, y + 4, 8, 14, 0x5A7A46);
  });

  // desk clutter: mugs and a toolbox
  pixelRect(g, 556, 164, 10, 12, T.tan); pixelRect(g, 566, 167, 3, 5, T.tan);
  pixelRect(g, 286, 370, 10, 12, T.tan); pixelRect(g, 296, 373, 3, 5, T.tan);
  prop(g, 214, 372, 46, 26, 0x8C3B2E, 0xA34838);                // red toolbox
  pixelRect(g, 228, 368, 18, 5, T.coal);
  root.addChild(g);

  for (const [name, s] of Object.entries(STATIONS)) {
    root.addChild(label(s.label, s.x, s.y + 30, 8, T.parchment));
    const lamp = new PIXI.Graphics();          // §2.2: only ONE glows at a time
    pixelRect(lamp, s.x - 20, s.y - 54, 40, 5, T.work);
    pixelRect(lamp, s.x - 14, s.y - 49, 28, 4, T.work, .45);
    pixelRect(lamp, s.x - 8, s.y - 45, 16, 4, T.work, .2);
    lamp.alpha = 0;
    root.addChild(lamp);
    lamps[name] = lamp;
  }

  root.addChild(label("SHIP OUT", 894, 104, 8, 0x2A211C));
  root.addChild(label("MAIL SLOT", 894, 254, 7, T.parchment));

  monitors = new PIXI.Graphics();  root.addChild(monitors);
  needle   = new PIXI.Graphics();  root.addChild(needle);

  // neon sign -- the one permitted glow lives here (§2.2)
  neon = new PIXI.Text({ text: "THE GARAGE", style: {
    fontFamily: "Silkscreen, monospace", fontSize: 26, fill: T.warm, letterSpacing: 2 } });
  neon.x = 390; neon.y = 44; neon.alpha = .92;
  root.addChild(neon);

  chalk = new PIXI.Text({ text: "", style: {
    fontFamily: "IBM Plex Mono, monospace", fontSize: 9, fill: 0x4A423A,
    wordWrap: true, wordWrapWidth: 166 } });
  chalk.x = 68; chalk.y = 130;
  root.addChild(chalk);

  tally = new PIXI.Text({ text: "", style: {
    fontFamily: "Silkscreen, monospace", fontSize: 11, fill: 0x4A423A } });
  tally.x = 68; tally.y = 186;
  root.addChild(tally);
}

/* --------------------------------------------------------------- the car */

/* The job itself, made physical. A task arriving is a car rolling onto the
 * lift; a task shipping is the car leaving through the door. It is the one
 * object on the floor that a viewer can follow without knowing anything. */
function makeCar() {
  const c = new PIXI.Container();
  const g = new PIXI.Graphics();
  pixelRect(g, -21, 4, 42, 5, T.shadow, .3);
  prop(g, -20, -12, 40, 20, 0x9B3B2E, 0xB8483A, 6);
  prop(g, -12, -20, 24, 10, T.parchment, null, 0);
  pixelRect(g, -18, -13, 36, 3, 0x7A2C22);
  pixelRect(g, -22, -8, 4, 8, T.coal); pixelRect(g, 18, -8, 4, 8, T.coal);
  pixelRect(g, 17, -11, 5, 4, T.work);        // headlamps -- the car is running
  pixelRect(g, -22, -11, 5, 4, T.work);
  c.addChild(g);
  c.x = CAR_HOME.x; c.y = CAR_HOME.y;
  c.visible = false;
  return c;
}

/* --------------------------------------------------------------- avatars */

function makeAvatar(name) {
  const ch = CHARS[name];
  const c = new PIXI.Container();
  const g = new PIXI.Graphics();
  const w = ch.w, top = -ch.tall;

  pixelRect(g, -w / 2 - 1, 3, w + 2, 4, T.shadow, .32);          // ground shadow
  pixelRect(g, -w / 2 + 1, -8, 4, 11, 0x2E2A26);                 // legs
  pixelRect(g, w / 2 - 5, -8, 4, 11, 0x2E2A26);
  pixelRect(g, -w / 2 - 1, top + 9, w + 2, 20, T.coal);          // torso outline
  pixelRect(g, -w / 2, top + 10, w, 18, ch.shirt);               // torso
  pixelRect(g, -w / 2 - 3, top + 12, 3, 11, ch.shirt);           // arms
  pixelRect(g, w / 2, top + 12, 3, 11, ch.shirt);
  pixelRect(g, -w / 2 - 3, top + 22, 3, 4, ch.skin);             // hands
  pixelRect(g, w / 2, top + 22, 3, 4, ch.skin);
  pixelRect(g, -6, top - 1, 12, 13, T.coal);                     // head outline
  pixelRect(g, -5, top, 10, 11, ch.skin);                        // face
  pixelRect(g, -3, top + 4, 2, 2, T.coal); pixelRect(g, 1, top + 4, 2, 2, T.coal);
  pixelRect(g, -6, top - 3, 12, 6, ch.hair);                     // hair

  // one signature prop each (§4.3) -- the tell that survives at this size
  if (ch.prop === "cap") {
    pixelRect(g, -7, top - 4, 14, 5, T.fail);                    // Raju's cap
    pixelRect(g, 5, top - 1, 5, 3, T.fail);
  }
  if (ch.prop === "ponytail") {
    pixelRect(g, 5, top - 2, 4, 13, ch.hair);                    // Chutki
    pixelRect(g, 7, top + 6, 3, 6, ch.hair);
  }
  if (ch.prop === "glasses") {
    pixelRect(g, -6, top + 3, 12, 1, T.tan);                     // Bholu
    pixelRect(g, -4, top + 3, 3, 3, T.parchment, .8);
    pixelRect(g, 1, top + 3, 3, 3, T.parchment, .8);
  }
  if (ch.prop === "hood") pixelRect(g, -8, top - 4, 16, 8, 0x8C4322);   // Kalia
  if (ch.prop === "clipboard") pixelRect(g, w / 2 + 1, top + 15, 6, 8, T.parchment);
  if (ch.prop === "none") pixelRect(g, -w / 2, top + 10, w, 3, 0x3E6038); // Bheem's collar
  c.addChild(g);
  c.body = g;            // bobbed by the animation modes; the tag must not move

  // Role ids had to be truncated to 6 chars to stop the couch tags colliding.
  // The crew names are all <=6 already, so DESIGN §4.3's always-visible tag
  // finally gets to show the whole name -- on a parchment backer, because tan
  // text on concrete is the one pairing in this palette that fails to read.
  const tagWrap = new PIXI.Container();
  const t = label(shown(name), 0, 0, 7, 0x2A211C);
  const back = new PIXI.Graphics();
  pixelRect(back, t.x - 3, -2, t.width + 6, 11, T.parchment, .92);
  tagWrap.addChild(back); tagWrap.addChild(t);
  tagWrap.y = 8;
  c.addChild(tagWrap);

  const glow = new PIXI.Graphics();
  pixelRect(glow, -14, 6, 28, 4, T.work, .55);
  glow.alpha = 0;
  c.addChildAt(glow, 0);
  c.glow = glow;
  return c;
}

/* The one- or two-word popup over whoever is working. This is the piece that
 * makes the floor readable on its own: PLAN / FIND / BUILD / TEST / REVIEW /
 * RECORD says what the real software is doing right now, with no feed. */
function makeBubble(name) {
  const s = STATIONS[name];
  const c = new PIXI.Container();
  const t = label(s.word, 0, 3, 9, 0x2A211C);
  const g = new PIXI.Graphics();
  const w = t.width + 12, h = 16;
  pixelRect(g, -w / 2 - 1, -1, w + 2, h + 2, T.coal);
  pixelRect(g, -w / 2, 0, w, h, T.parchment);
  pixelRect(g, -3, h, 6, 4, T.parchment);
  pixelRect(g, -3, h + 4, 6, 1, T.coal);
  c.addChild(g); c.addChild(t);
  c.visible = false;
  return c;
}

/* --------------------------------------------------------------- motion */

/* Three states, and the walk carries what to do on arrival. Without that the
 * scene would have to remember "who was heading where to do what", which is
 * exactly the sort of bookkeeping that drifts out of step with the events. */
function walkTo(name, x, y, thenMode = "idle") {
  const a = avatars[name];
  a.target = { x, y };
  a.mode = "walk";
  a.then = thenMode;
  if (quiet()) { a.x = x; a.y = y; a.target = null; a.mode = thenMode; }
}

function sendHome(role) {
  const st = STATIONS[role];
  if (st) walkTo(role, st.x, st.y, "idle");
}

function driveTo(x, y) {
  car.visible = true;
  car.target = { x, y };
  if (quiet()) { car.x = x; car.y = y; car.target = null; }
}

function throwPaper(fromKey, toKey, color = T.parchment) {
  if (quiet()) return;
  const from = fromKey === "couch" ? COUCH : STATIONS[fromKey];
  const to   = toKey === "couch" ? COUCH : STATIONS[toKey];
  if (!from || !to) return;
  const g = new PIXI.Graphics();
  pixelRect(g, -5, -4, 10, 8, T.coal);
  pixelRect(g, -4, -3, 8, 6, color);
  g.x = from.x; g.y = from.y - 26;
  layers.fx.addChild(g);
  flying.push({ g, t: 0, from: { x: from.x, y: from.y - 26 }, to: { x: to.x, y: to.y - 26 } });
}

/* The signature element (§4.5). The one screen shake and the one time-freeze
 * in the entire project -- spent here because the eval gate is the project's
 * whole identity, so a verdict cannot be a toast notification. */
/* Repo mode's third outcome gets its own word on the stamp. "UNVERIFIED" is
 * long, so it stamps smaller -- but it stamps, because a viewer must never
 * have to guess which of the three happened. */
function verdictWord(p) {
  if (p.verdict === "unverified") return "UNVERIFIED";
  if (p.verdict === "pass" || p.verdict === "accept") return "PASS";
  return p.gate === "review" ? "REJECT" : "FAIL";
}

function slamStamp(text, ok) {
  if (SILENT) return;                            // folding, not playing
  const wrap = new PIXI.Container();
  wrap.x = CAR_HOME.x; wrap.y = CAR_HOME.y - 30;
  const unver = text === "UNVERIFIED";
  const t = new PIXI.Text({ text, style: {
    fontFamily: "Silkscreen, monospace", fontSize: unver ? 30 : 48,
    fill: unver ? T.work : (ok ? T.pass : T.fail) } });
  t.anchor.set(.5); t.rotation = -6 * Math.PI / 180;
  wrap.addChild(t);
  wrap.scale.set(REDUCED ? 1 : 2.2);
  wrap.alpha = REDUCED ? 1 : 0;
  layers.fx.addChild(wrap);
  wrap.stampT = 0;
  wrap.isStamp = true;
  if (!quiet()) { freezeUntil = performance.now() + MICRO_MS; shake = 2; }
  setTimeout(() => wrap.destroy(), STAMP_MS + 500);
}

function setWorkLamp(name) {                     // §2.2: singular --work glow
  for (const [k, lamp] of Object.entries(lamps)) lamp.alpha = k === name ? 1 : 0;
  for (const [k, a] of Object.entries(avatars)) a.glow.alpha = k === name ? 1 : 0;
  for (const [k, b] of Object.entries(bubbles)) b.visible = k === name;
  // the crew cards follow the same singular rule, so the roster also answers
  // "who is working right now" without the viewer hunting for the lit lamp
  for (const k of AGENTS) {
    for (const id of ["who-", "card-"]) {
      const el = document.getElementById(id + k);
      if (el) el.classList.toggle("on", k === name);
    }
  }
}

/* -------------------------------------------------------------- reducer */

const S = { task:"—", job:"—", attempt:"—", tests:[], review:[], spend:0,
            shipped:0, failed:0, retries:0, active:null };

/* The scene reads ONE event shape and knows nothing about where it came from
 * (see source.js). Everything below is a function of { worker, action, job,
 * status, result } -- swap the mock feed for a WebSocket and not a line here
 * changes. */
function applyToScene(e) {
  if (!e || e.error || e.meta) return;
  const role = e.worker ? roleOf(e.worker) : null;

  switch (e.status) {
    case "job_start":
      // a new file rolls in and parks on the lift, where it stays
      Object.assign(S, { job: e.job || "—", task: e.job || "—", attempt: "—",
                         tests: [], review: [], retries: 0, active: null });
      chalk.text = String(e.job || "").replace(/__/g, " ");
      tally.text = "";
      car.x = 860; car.y = CAR_HOME.y; car.exiting = false;
      driveTo(CAR_HOME.x, CAR_HOME.y);
      setWorkLamp(null);
      if (!quiet()) { dimmer.alpha = .1; setTimeout(() => dimmer.alpha = 0, MICRO_MS); }
      break;

    case "start": {
      if (!role) break;
      // only ever one at a time: whoever was working goes back to their bench
      if (S.active && S.active !== role) sendHome(S.active);
      S.active = role;
      const bay = BAY[role];
      walkTo(role, bay.x, bay.y, "work");
      setWorkLamp(role);                 // amber lamp, glow and popup, singular
      break;
    }

    case "result": {
      if (e.result) {
        const gate = role === "reviewer" ? S.review : S.tests;
        gate.push(e.result);
        slamStamp(verdictWord({ verdict: e.result,
                                gate: role === "reviewer" ? "review" : "tests" }),
                  ["pass", "accept"].includes(e.result));
        if (!["pass", "accept"].includes(e.result)) {
          S.retries++;
          tally.text = "|".repeat(S.retries).replace(/(\|{5})/g, "$1 ");
          throwPaper(role, "builder", T.fail);   // it goes back to Kalia
        }
      }
      break;
    }

    case "done":
      if (!role) break;
      sendHome(role);
      if (S.active === role) { S.active = null; setWorkLamp(null); }
      break;

    case "job_end":
      if (e.action === "ship") {
        S.shipped++; driveTo(DOOR.x, DOOR.y); car.exiting = true;
        neon.tint = T.pass; setTimeout(() => neon.tint = 0xFFFFFF, 500);
      } else {
        S.failed++; driveTo(834, 452);          // parked by the bin
        chalk.text += "\n\u2717 " + (e.result || "failed");
      }
      AGENTS.forEach(sendHome);
      S.active = null; setWorkLamp(null);
      break;
  }
}

/* ---------------------------------------------------------------- ticker */

function moveToward(o, speed, dt) {
  if (!o.target) return;
  const dx = o.target.x - o.x, dy = o.target.y - o.y;
  const d = Math.hypot(dx, dy);
  if (d < 1.5) { o.x = o.target.x; o.y = o.target.y; o.target = null; return; }
  const step = Math.min(d, speed * dt);
  o.x = Math.round(o.x + (dx / d) * step);
  o.y = Math.round(o.y + (dy / d) * step);
}

function tick(ticker) {
  const now = performance.now();
  const dt = ticker.deltaMS / 1000;
  if (now < freezeUntil) return;                 // the world waits for the gate

  AGENTS.forEach((name, i) => {
    const a = avatars[name];
    moveToward(a, WALK_SPEED, dt);
    if (!a.target && a.mode === "walk") a.mode = a.then || "idle";

    // Three animations, all one pixel of vertical bob at different speeds.
    // Cheap, and it is the whole difference between a sprite sitting on the
    // floor and a person standing at a bench doing something.
    if (a.body) {
      let off = 0;
      if (!REDUCED) {
        if (a.mode === "walk") off = -(Math.floor(now / 110) % 2);
        else if (a.mode === "work") off = -(Math.floor(now / 150) % 2) * 2;
        else off = Math.sin(now / 900 + i * 1.7) > 0.86 ? -1 : 0;  // idle breath
      }
      a.body.y = off;
    }
    const b = bubbles[name];
    if (b) { b.x = a.x; b.y = a.y - CHARS[name].tall - 24; }
  });
  moveToward(car, CAR_SPEED, dt);
  if (car.exiting && !car.target) { car.visible = false; car.exiting = false; }

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
  const screens = [[574,128,58,44],[638,122,58,50],[702,128,58,44]];
  screens.forEach(([x, y, w, h], i) => {
    pixelRect(monitors, x + 3, y + 3, w - 6, h - 6,
              lit ? T.wire : T.coalLift, lit ? .30 + .12 * Math.sin(now / 220 + i) : 1);
    if (!lit) return;
    for (let ln = 0; ln < 4; ln++)               // code lines, scrolling
      pixelRect(monitors, x + 6, y + 8 + ln * 8 + (Math.floor(now / 300 + i) % 2), 
                14 + ((ln + i) % 3) * 11, 2, T.wire, .8);
  });

  needle.clear();
  if (S.active === "tester") needle.sweepUntil = now + 200;
  if (needle.sweepUntil && now < needle.sweepUntil) {
    const a = -Math.PI * .75 + Math.PI * .5 * (1 + Math.sin(now / 90));
    needle.moveTo(609, 372).lineTo(609 + Math.cos(a) * 20, 372 + Math.sin(a) * 20)
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

const hex = n => "#" + n.toString(16).padStart(6, "0");
const tms = e => (typeof e.ts === "number" ? e.ts : Date.parse(e.ts || 0)) || 0;

/* ------------------------------------------------------------ the replay
 *
 * FR-26..29. The whole thing rests on one property: scene state at any moment
 * is a pure fold over events[0..n]. There is no snapshotting and none is
 * needed -- a 20k-event night folds in a few milliseconds because applying an
 * event is a handful of assignments.
 *
 * Live and replay stay one code path (the log is the only input either way).
 * Dragging the tape detaches from the tail; "go live" reattaches. */
const LOG = [];              // every event received, in order
let cursor = 0;              // how many of LOG have been applied
let clock = 0;               // the virtual moment being shown, in ms
let playing = false, speed = 1, live = true;
const FEED_TAIL = 250;       // rows kept in the DOM after a re-fold

function runSpan() {
  if (!LOG.length) return [0, 1];
  return [tms(LOG[0]), Math.max(tms(LOG[LOG.length - 1]), tms(LOG[0]) + 1)];
}

const S0 = () => ({ task:"—", attempt:"—", tests:[], review:[], spend:0,
                    shipped:0, failed:0, retries:0, active:null });

function resetScene() {
  for (const c of [...layers.fx.children]) c.destroy();
  flying.length = 0;
  AGENTS.forEach(n => {                    // home is your own workstation
    const st = STATIONS[n];
    avatars[n].x = st.x; avatars[n].y = st.y;
    avatars[n].target = null; avatars[n].mode = "idle";
  });
  setWorkLamp(null);
  car.visible = false; car.target = null; car.exiting = false;
  car.x = CAR_HOME.x; car.y = CAR_HOME.y;
  chalk.text = ""; tally.text = ""; dimmer.alpha = 0;
  needle.sweepUntil = 0; freezeUntil = 0; shake = 0;
  layers.root.x = layers.root.y = 0;
  Object.assign(S, S0());
}

/* Rewind or jump: rebuild from event zero. Only ever called when moving
 * BACKWARDS or landing somewhere new -- going forwards just keeps applying. */
/* Fold exactly N events. Index-based, because time cannot separate them:
 * a stub run emits a whole stage inside one millisecond, so stepping by
 * timestamp advances the lot and you never see the individual moves. */
function foldToCount(n, clockAt = null) {
  n = Math.max(0, Math.min(LOG.length, n));
  SILENT = true;
  resetScene();
  for (let i = 0; i < n; i++) applyToScene(LOG[i]);
  cursor = n;
  SILENT = false;
  finishFold(clockAt !== null ? clockAt
             : (n ? tms(LOG[n - 1]) : runSpan()[0]));
}

function foldTo(t) {
  let i = 0;
  while (i < LOG.length && tms(LOG[i]) <= t) i++;
  foldToCount(i, t);
}

function finishFold(t) {
  const shownRows = LOG.slice(Math.max(0, cursor - FEED_TAIL), cursor);
  $("feed").innerHTML = "";
  shownRows.forEach(feedRow);
  clock = t;
  // landing exactly on a verdict should still show its stamp -- that is the
  // whole point of clicking a red notch
  const last = LOG[cursor - 1];
  if (last && last.result) {
    slamStamp(verdictWord({ verdict: last.result,
                            gate: roleOf(last.worker) === "reviewer"
                                  ? "review" : "tests" }), isGreen(last));
  }
  paintHud(); drawTape(); paintReadout();
}

function advanceTo(t) {
  if (t < clock) return foldTo(t);
  while (cursor < LOG.length && tms(LOG[cursor]) <= t) {
    applyToScene(LOG[cursor]); feedRow(LOG[cursor]); cursor++;
    trimFeed();
  }
  clock = t;
  paintHud(); drawTape(); paintReadout();
}

function trimFeed() {
  const f = $("feed");
  while (f.childElementCount > FEED_TAIL * 2) f.removeChild(f.firstChild);
}

function setLive(on) {
  live = on;
  $("golive").classList.toggle("on", on);
  neon.text = on ? "THE GARAGE" : "REPLAY";      // scene and chrome agree (§5.2)
  neon.style.fill = on ? T.warm : T.tan;
  $("dot").classList.toggle("live", on && !!source);
}

/* ------------------------------------------------------------------ tape */

/* Styled as tape, not a media player (DESIGN §5.2): event density as ticks,
 * verdicts as green/red notches you can click, cumulative spend as a line
 * across the same x axis (FR-29), and a --wire playhead. */
function drawTape() {
  const cv = $("tape"), ctx = cv.getContext("2d");
  const dpr = devicePixelRatio || 1;
  const w = cv.clientWidth, h = cv.clientHeight;
  if (cv.width !== w * dpr || cv.height !== h * dpr) {
    cv.width = w * dpr; cv.height = h * dpr;
  }
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  ctx.clearRect(0, 0, w, h);
  ctx.fillStyle = "#211B19"; ctx.fillRect(0, 0, w, h);
  if (!LOG.length) return;

  const [t0, t1] = runSpan();
  const X = t => 2 + ((t - t0) / (t1 - t0)) * (w - 4);

  // cumulative spend (FR-29)
  let usd = 0, maxUsd = 0;
  const pts = LOG.map(e => {
    if (typeof e.usd === "number") usd = e.usd;
    maxUsd = Math.max(maxUsd, usd);
    return [X(tms(e)), usd];
  });
  if (maxUsd > 0) {
    ctx.beginPath();
    ctx.moveTo(2, h - 3);
    pts.forEach(([x, u]) => ctx.lineTo(x, h - 3 - (u / maxUsd) * (h - 16)));
    ctx.lineTo(w - 2, h - 3);
    ctx.fillStyle = "rgba(216,193,138,.13)"; ctx.fill();
    ctx.beginPath();
    pts.forEach(([x, u], i) => { const y = h - 3 - (u / maxUsd) * (h - 16);
      i ? ctx.lineTo(x, y) : ctx.moveTo(x, y); });
    ctx.strokeStyle = "#D8C18A"; ctx.lineWidth = 1; ctx.stroke();
  }

  // event density
  ctx.fillStyle = "rgba(184,166,139,.5)";
  LOG.forEach(e => ctx.fillRect(Math.round(X(tms(e))), h - 9, 1, 6));

  // verdict notches -- one click jumps to any of them
  // Draw greens first so a red sharing the same pixel is the one you can see.
  // Two verdicts 4ms apart are one pixel; hiding the failure under the pass
  // would make the tape lie about where the trouble is.
  const notches = LOG.filter(isNotch);
  [...notches].sort((a, b) => isRed(a) - isRed(b)).forEach(e => {
    ctx.fillStyle = notchColour(e);
    ctx.fillRect(Math.round(X(tms(e))) - 1, 3, 3, h - 14);
  });

  // playhead
  const px = Math.round(X(Math.min(Math.max(clock, t0), t1)));
  ctx.fillStyle = "#4D8FDB";
  ctx.fillRect(px, 0, 1, h);
  ctx.fillRect(px - 3, 0, 7, 4);
}

function tapeTimeAt(clientX) {
  const cv = $("tape"), r = cv.getBoundingClientRect();
  const [t0, t1] = runSpan();
  const k = Math.min(1, Math.max(0, (clientX - r.left - 2) / (r.width - 4)));
  return t0 + k * (t1 - t0);
}

/* Which verdict did you mean?
 *
 * Snapping is in PIXELS, not milliseconds: a tests-pass and the review-reject
 * that followed it can be 4ms apart, which is the same pixel on any tape you
 * can fit on a screen, and picking "nearest in time" there is a coin flip.
 *
 * Within one pixel-cluster, RED WINS. The whole point of the notches is "click
 * a red one to jump to the failure" -- so if a failure shares a pixel with a
 * pass, the failure is what you asked for. Ties beyond that go to the latest,
 * so you land on the cluster's outcome rather than its first step. */
const isNotch = e => !!e.result;          // verdicts and job outcomes only
const isRed = e => ["fail", "reject"].includes(e.result);
/* Repo mode adds a third verdict: "unverified" -- no regressions, but nothing
 * proving the change did anything. It is neither a pass nor a failure, and
 * drawing it green would make the tape claim a fix that was never shown. */
const isGreen = e => ["pass", "accept"].includes(e.result);
const notchColour = e => isRed(e) ? "#D95A5A" : isGreen(e) ? "#46B46A" : "#F0B24B";

function snapToNotch(t) {
  const [t0, t1] = runSpan();
  const w = Math.max(40, $("tape").clientWidth - 4);
  const tol = ((t1 - t0) / w) * 5;               // 5px of slack
  const near = LOG.filter(e => isNotch(e) && Math.abs(tms(e) - t) <= tol);
  if (!near.length) return null;
  const reds = near.filter(isRed);
  const pool = reds.length ? reds : near;
  const e = pool[pool.length - 1];
  return { t: tms(e), e };
}

function paintReadout() {
  const [t0, t1] = runSpan();
  const at = LOG[Math.max(0, cursor - 1)];
  $("count").textContent = `${cursor} / ${LOG.length}`;
  // ts is a number now (the scene shape), not an ISO string off the wire
  $("stamp").textContent = at ? new Date(tms(at)).toTimeString().slice(0, 8)
                              : "--:--:--";
  const secs = Math.round((Math.min(clock, t1) - t0) / 1000);
  $("elapsed").textContent = `+${String(Math.floor(secs / 60)).padStart(2,"0")}:${String(secs % 60).padStart(2,"0")}`;
}

/* Hovering a mechanic (FR-28). Hit-testing in stage space against a DOM
 * tooltip beats making six sprites interactive: the scene is integer-scaled,
 * so one divide converts the pointer and nothing has to track scale changes. */
function stagePoint(ev) {
  const cv = app.canvas, r = cv.getBoundingClientRect();
  const k = r.width / W;
  return { x: (ev.clientX - r.left) / k, y: (ev.clientY - r.top) / k };
}

function agentAt(pt) {
  let best = null;
  for (const k of AGENTS) {
    const a = avatars[k];
    const dx = pt.x - a.x, dy = pt.y - (a.y - CHARS[k].tall / 2);
    const d = Math.hypot(dx, dy * 0.8);
    if (d < 22 && (!best || d < best.d)) best = { k, d };
  }
  return best && best.k;
}

function lastEventFor(role) {
  for (let i = cursor - 1; i >= 0; i--)
    if (LOG[i].worker && roleOf(LOG[i].worker) === role) return LOG[i];
  return null;
}

function wireHover() {
  const tip = $("tip");
  app.canvas.addEventListener("mousemove", ev => {
    const k = agentAt(stagePoint(ev));
    if (!k) { tip.classList.remove("on"); app.canvas.style.cursor = ""; return; }
    const e = lastEventFor(k);
    const kv = e ? [e.action, e.status, e.result].filter(Boolean).join(" · ") : "";
    tip.innerHTML =
      `<b>${shown(k)}</b> <span class="tr">${CHARS[k].role}</span>` +
      `<div class="td">${ROLES[k]}</div>` +
      (e ? `<div class="te">${kv || "waiting"}</div>` +
           `<div class="tl">click to see what it wrote &rarr;</div>`
         : `<div class="te">nothing yet at this point in the run</div>`);
    tip.classList.add("on");
    const pad = 14;
    tip.style.left = Math.min(ev.clientX + pad, innerWidth - tip.offsetWidth - 8) + "px";
    tip.style.top = Math.max(8, ev.clientY - tip.offsetHeight - pad) + "px";
    app.canvas.style.cursor = "pointer";
  });
  app.canvas.addEventListener("mouseleave", () => $("tip").classList.remove("on"));
  app.canvas.addEventListener("click", ev => {
    const k = agentAt(stagePoint(ev));
    const e = k && lastEventFor(k);
    if (e) showArtifact(e);
  });
}

/* ------------------------------------------------- artifacts (FR-28) */

/* Payloads carry pointers, never blobs (ADR-5), so the diff or the test log is
 * fetched only when someone actually asks to see it. */
async function showArtifact(scene) {
  // The scene never needs the backend's payload; the panel does. Carrying the
  // untranslated event on `raw` is what keeps that true both ways.
  const ev = scene.raw || scene;
  const path = (ev.payload || {}).artifact;
  const box = $("art"), body = $("artbody");
  $("arttitle").textContent = scene.worker
    ? `${shown(roleOf(scene.worker))} · ${scene.action || scene.status}`
    : `${scene.action || scene.status}`;
  box.classList.add("open");
  const kv = Object.entries(ev.payload || scene)
    .filter(([k]) => k !== "raw")
    .map(([k, v]) => `${k} = ${typeof v === "object" ? JSON.stringify(v) : v}`).join("\n");
  if (!path) { body.textContent = kv || "(no payload)"; return; }
  body.textContent = kv + "\n\nloading " + path + " …";
  try {
    const r = await fetch(`/api/runs/${encodeURIComponent(currentRun)}/artifacts/${path}`);
    body.textContent = kv + "\n\n── " + path + " ──\n" + (r.ok
      ? await r.text() : `(${r.status} — artifact not on disk)`);
  } catch (err) {
    body.textContent = kv + "\n\n(could not fetch " + path + ": " + err.message + ")";
  }
}

/* --------------------------------------------------------------- roster */

function portrait(k) {
  const c = CHARS[k];
  return `<span class="pt" style="background:${hex(c.shirt)}">` +
         `<i class="ht" style="background:${hex(c.hair)}"></i>` +
         `<i class="fc" style="background:${hex(c.skin)}"></i></span>`;
}

function buildCrew() {
  const box = $("crew");
  box.innerHTML = "";
  for (const k of AGENTS) {
    const row = document.createElement("div");
    row.className = "who"; row.id = "who-" + k;
    row.innerHTML =
      `<span class="sw" style="background:${hex(CHARS[k].shirt)}"></span>` +
      `<span><span class="nm">${shown(k)}</span> <span class="rid">${k}</span>` +
      `<div class="rl">${ROLES[k]}</div></span>`;
    box.appendChild(row);
  }
}

function buildStrip() {
  const box = $("strip");
  box.innerHTML = "";
  for (const k of AGENTS) {
    const card = document.createElement("div");
    card.className = "card"; card.id = "card-" + k;
    card.innerHTML = portrait(k) +
      `<span class="meta"><span class="cn">${shown(k)}</span>` +
      `<span class="cr">${CHARS[k].role}</span>` +
      `<span class="cs"><i class="dotp"></i><span class="st">idle</span></span></span>`;
    // FR-28: what was this mechanic last doing, and show me the artifact
    card.onclick = () => {
      for (let i = cursor - 1; i >= 0; i--)
        if (LOG[i].agent === k) return showArtifact(LOG[i]);
    };
    box.appendChild(card);
  }
}

function paintHud() {
  $("task").textContent = S.task;
  $("attempt").textContent = S.attempt;
  $("gtests").innerHTML = S.tests.map(stampGlyph).join("") || "·";
  $("greview").innerHTML = S.review.map(stampGlyph).join("") || "·";
  $("spend").textContent = "$" + S.spend.toFixed(4);
  $("shipped").textContent = S.shipped;
  $("failed").textContent = S.failed;
  for (const k of AGENTS) {
    const card = $("card-" + k);
    if (card) card.querySelector(".st").textContent =
      S.active === k ? STATIONS[k].word.toLowerCase() : "idle";
  }
}

function feedRow(e) {
  const cls = isGreen(e) ? "pass" : isRed(e) ? "fail" : "";
  const who = e.worker ? shown(roleOf(e.worker)) : "garage";
  const what = [e.action, e.status].filter(Boolean).join(" ");
  const kv = [e.job ? `job=${e.job}` : "", e.result ? `result=${e.result}` : ""]
    .filter(Boolean).join(" ");
  const hasArt = !!((e.raw || {}).payload || {}).artifact;
  const row = document.createElement("div");
  row.className = "row " + cls + (hasArt ? " has" : "");
  row.innerHTML =
    `<span class="t">${new Date(tms(e)).toTimeString().slice(0, 8)}</span>` +
    `<span class="ag">${who}</span><span class="ty">${what}</span>` +
    `<span class="kv">${kv.replace(/[<>&]/g, c => ({'<':'&lt;','>':'&gt;','&':'&amp;'}[c]))}</span>`;
  row.onclick = () => showArtifact(e);
  const f = $("feed");
  const stuck = f.scrollTop + f.clientHeight >= f.scrollHeight - 30;
  f.appendChild(row);
  if (stuck) f.scrollTop = f.scrollHeight;
}

/* ------------------------------------------------------------- transport */

/* The only place that knows a data source exists. Everything above consumes
 * the normalized shape and would not notice the difference between a mock
 * night and a real one -- which is the point: the backend can be wired in
 * later without redrawing anything. */
let source = null, currentRun = null;

function openSource(kind, id) {
  if (source) { source.stop(); source = null; }
  currentRun = id || null;
  LOG.length = 0; cursor = 0; clock = 0; playing = false;
  $("play").textContent = "▶";
  resetScene(); $("feed").innerHTML = "";
  $("art").classList.remove("open");
  setLive(true);
  paintHud(); drawTape(); paintReadout();
  $("status").textContent = kind === "mock" ? "mock feed" : "connecting…";
  $("dot").className = "dot";

  const onEvent = e => {
    if (e.error) { $("status").textContent = e.error; return; }
    if (e.meta) {
      if (e.meta === "connected") {
        $("status").textContent = "live"; $("dot").className = "dot live";
      }
      if (e.meta === "closed") $("dot").classList.remove("live");
      if (e.meta === "mock") $("dot").className = "dot live";
      return;
    }
    LOG.push(e);
    if (live) {
      // Mock events arrive one at a time, so following the tail IS watching
      // it happen. A finished real run arrives in one burst, so we stay parked
      // at the start and let the tape drive -- the week-5 rule, unchanged.
      if (kind === "mock") advanceTo(tms(e));
      else if (LOG.length === 1) { clock = tms(e); }
    }
    drawTape(); paintReadout();
  };

  source = kind === "mock" ? new MockSource(onEvent) : new LiveSource(id, onEvent);
  source.start();
}

function wireTransport() {
  $("play").onclick = () => {
    if (!LOG.length) return;
    const [, t1] = runSpan();
    if (clock >= t1) foldToCount(0);            // replay from the top
    playing = !playing;
    $("play").textContent = playing ? "❚❚" : "▶";
    if (playing) setLive(false);
  };
  $("restart").onclick = () => { foldToCount(0); playing = false;
                                 $("play").textContent = "▶"; setLive(false); };
  $("stepb").onclick = () => { playing = false; $("play").textContent = "▶";
                               setLive(false); foldToCount(cursor - 1); };
  $("stepf").onclick = () => { playing = false; $("play").textContent = "▶";
                               setLive(false); foldToCount(cursor + 1); };
  $("golive").onclick = () => {
    setLive(true); playing = false; $("play").textContent = "▶";
    if (LOG.length) advanceTo(runSpan()[1] + 1);
  };
  document.querySelectorAll("[data-speed]").forEach(b => {
    b.onclick = () => {
      speed = +b.dataset.speed;
      document.querySelectorAll("[data-speed]").forEach(x =>
        x.classList.toggle("on", x === b));
    };
  });
  $("artclose").onclick = () => $("art").classList.remove("open");

  const cv = $("tape");
  let dragging = false;
  const seekFromEvent = ev => {
    const t = tapeTimeAt(ev.clientX);
    const hit = snapToNotch(t);
    foldTo(hit ? hit.t : t);
    if (hit && !dragging) showArtifact(hit.e);   // click a red notch, see why
  };
  cv.onmousedown = ev => { dragging = false; playing = false;
                           $("play").textContent = "▶"; setLive(false);
                           seekFromEvent(ev); cv.setPointerCapture?.(ev.pointerId); };
  cv.onmousemove = ev => { if (ev.buttons & 1) { dragging = true; 
                           foldTo(tapeTimeAt(ev.clientX)); } };
  cv.onmouseup = ev => { if (dragging) { dragging = false; return; } };
  addEventListener("resize", drawTape);
  addEventListener("keydown", ev => {
    if (ev.target.tagName === "SELECT") return;
    if (ev.code === "Space") { ev.preventDefault(); $("play").click(); }
    // one event per press, whatever the clock says
    if (ev.key === "ArrowRight" && cursor < LOG.length) {
      playing = false; $("play").textContent = "\u25B6"; setLive(false);
      foldToCount(cursor + 1);
    }
    if (ev.key === "ArrowLeft" && cursor > 0) {
      playing = false; $("play").textContent = "\u25B6"; setLive(false);
      foldToCount(cursor - 1);
    }
  });
}

/* Playback rides the same ticker as the scene, so a paused replay freezes the
 * walk cycles too rather than leaving mechanics sliding around a dead clock. */
function tickPlayback(ticker) {
  if (!playing || !LOG.length) return;
  const [, t1] = runSpan();
  advanceTo(clock + ticker.deltaMS * speed);
  if (clock >= t1) { playing = false; $("play").textContent = "▶";
                     $("status").textContent = "end of run"; }
}

async function boot() {
  await app.init({ width: W, height: H, background: T.coal,
                   antialias: false, roundPixels: true, autoDensity: false });
  $("stage").appendChild(app.canvas);
  buildCrew(); buildStrip(); wireTransport(); wireHover();

  layers.root = new PIXI.Container();
  app.stage.addChild(layers.root);
  layers.root.addChild(buildFloor());
  buildStations(layers.root);
  layers.actors = new PIXI.Container(); layers.root.addChild(layers.actors);
  layers.fx = new PIXI.Container();     layers.root.addChild(layers.fx);
  layers.ui = new PIXI.Container();     layers.root.addChild(layers.ui);

  car = makeCar();
  layers.actors.addChild(car);

  dimmer = new PIXI.Graphics();
  pixelRect(dimmer, 0, 0, W, H, T.coal);
  dimmer.alpha = 0;
  layers.root.addChild(dimmer);

  AGENTS.forEach((n, i) => {
    const a = makeAvatar(n);
    const st = STATIONS[n]; a.x = st.x; a.y = st.y; a.mode = "idle";
    layers.actors.addChild(a); avatars[n] = a;
    const b = makeBubble(n);
    layers.ui.addChild(b); bubbles[n] = b;
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
  app.ticker.add(tickPlayback);

  const sel = $("runs");
  const mock = document.createElement("option");
  mock.value = "mock"; mock.textContent = "mock feed · no backend";
  sel.appendChild(mock);

  // The run list is a nicety, not a dependency: with no server at all the
  // mock feed still runs, which is what makes the garage buildable on its own.
  try {
    const runs = await (await fetch("/api/runs")).json();
    runs.forEach(r => {
      const o = document.createElement("option");
      o.value = r.run_id;
      o.textContent = `${r.run_id} · ${r.events} events` +
        (r.running ? " · running" : "") + (r.invalid ? " · INVALID" : "");
      sel.appendChild(o);
    });
  } catch { /* no server: mock only */ }

  const open = v => v === "mock" ? openSource("mock") : openSource("live", v);
  sel.onchange = () => open(sel.value);
  open(sel.value);

  // feed-only mode: the demo must work with the garage off (DESIGN §5.1)
  $("toggleFeed").onclick = () => {
    const on = document.body.dataset.feedOnly !== "1";
    document.body.dataset.feedOnly = on ? "1" : "0";
    $("toggleFeed").textContent = on ? "show garage" : "feed only";
  };
}
boot();
