# DESIGN.md
## Visual Design System — "The Garage"

> Canonical design source of truth for everything the user sees: the Pixi.js garage
> scene, the text feed, the HUD, the replay controls, and the project's README/site.
> Companion docs: `PRD.md` (what), `TAD.md` (how), `rules.md` (guardrails).
> If a component's look isn't derivable from this file's tokens, this file is
> incomplete — fix the file, don't freestyle the component.

---

## 1. Design thesis

**"A garage at 11pm where the machines do the work and the wall does the talking."**

Munder Difflin is a daytime sitcom office — warm cream walls, maroon-and-gold
corporate chrome, employees at desks. This project is deliberately the opposite
end of the founder fantasy: a **night-time startup garage**. Concrete floor,
one strip of neon, pizza box on the couch, and a whiteboard covered in the only
thing that matters — **the numbers**.

Three ideas everything derives from:

1. **The scene is dark, the data is lit.** The garage is drawn in low-key
   night tones; whatever is *true right now* (the active agent, the current
   verdict, the spend counter) is what glows. Attention = luminance.
2. **Verdicts are physical.** This project's whole identity is the eval gate,
   so pass/fail cannot be a toast notification — it's a **stamp slammed on
   paper** at the bench, green or red, with a screen-shake frame. The gate is
   the signature element; everything else stays quiet.
3. **Engineering honesty as an aesthetic.** No fake polish: the text feed is
   monospaced and unapologetic, counts are shown next to percentages, and the
   HUD displays real dollars with three decimals. The UI should *look* like it
   was built by someone who cares more about the metric than the gradient —
   because that's the story the project tells.

**Anti-goals:** corporate office anything (that's Munder Difflin's lane),
LimeZu or Office-adjacent visual references (license + identity, see rules.md
§2.2), cozy daylight pastel "Animal Crossing" warmth, and dashboard-template
chrome (cards with drop shadows, KPI tiles, gradient buttons).

---

## 2. Color tokens

Palette: **dark and cosy** — a workshop that has been running all night. Dark
surfaces are warm (**coal, walnut, dusty concrete**), never blue-violet and
never neutral gray. Mid-tones are lifted so the room stays readable while the
four status colors still pop off it. Exactly one warm accent (work light) and
two verdict colors that are never used for anything else.

*Revised from the original blue-violet night palette. The room reading as cold
and empty was the reason; the four meaning-carrying tokens below were carried
across unchanged in role, only retuned to sit on warm surfaces.*

### 2.1 Core palette

| Token | Hex | Name | Use |
|---|---|---|---|
| `--work` | `#F0B24B` | amber lamp | THE accent: active mechanic glow, current station lamp, links, focus rings, spend counter |
| `--pass` | `#46B46A` | ship green | pass stamp, solved counts, ship animation. **Verdicts only** |
| `--fail` | `#D95A5A` | reject red | fail stamp, reject reasons, failure types. **Verdicts only** |
| `--wire` | `#4D8FDB` | screen blue | screens, wires and data accents: monitors, cost sparkline, scrubber playhead |
| `--coal` | `#211B19` | coal | app background, outer walls, deep shadow, prop outlines |
| `--walnut` | `#4A382D` | walnut | wood beams, desks, benches, trim, panel surfaces |
| `--concrete` | `#6D5B48` | dusty concrete | the floor and its tiling |
| `--tan` | `#B8A68B` | tan metal | shelves, filing boxes, stools, props, secondary text |
| `--parchment` | `#E8DAB9` | parchment | primary text, whiteboard fill, paper sprites, name-tag and speech-bubble backers |
| `--warm` | `#D8C18A` | warm light | ambient bulbs, soft lamp glow, highlights, the neon sign |

Derived shades (`--coal-lift`, `--walnut-lift`, plus the concrete light/dark
pair used by the scene) are **mixed from the ten above**. No eleventh hue is
invented anywhere in the codebase.

### 2.2 Rules of use

- `--work` is singular: at any moment, at most **one** thing on screen is
  sodium-orange — the thing currently working. If two things glow, the design
  is wrong.
- `--pass`/`--fail` appear **only** for gate verdicts and their aggregates.
  Never for buttons, never for "online" status (that's `--wire`), never
  decoratively. A user must be able to trust that green = a gate said yes.
- No pure black, no pure white, no gradients on surfaces. Gradients are
  permitted in exactly one place: the neon sign glow (§4.2). The sign glows
  `--warm`, **not** `--fail`: a red neon would spend a verdict colour on
  decoration, which §2.2 forbids however good it looks.
- Clothing may not borrow a status colour. The builder's hoodie and the
  tester's tee are pulled down to rust and ochre for exactly this reason —
  beside the real `--work` amber they must read as cloth, not as status.
- Contrast floor: body text `--paper` on `--ink-1/2` (≥ 10:1); `--paper-dim`
  reserved for ≥ 14px secondary text.

---

## 3. Typography

Two faces, both free (Google Fonts), pixel-era but legible:

| Token | Face | Role |
|---|---|---|
| `--font-display` | **"Silkscreen"** | Display/chrome: panel titles, station labels, the neon sign, stamp text, big numbers. Always uppercase, always small sizes-in-big-scale (it's a pixel face — render at multiples of 8px: 16/24/32) |
| `--font-data` | **"IBM Plex Mono"** | Everything else: feed lines, HUD values, reasons, tooltips, docs code. The project speaks monospace because its native tongue is a JSONL log |

Type scale (px): `12` (timestamps, sprite name tags) · `14` (feed body, HUD labels)
· `16` (feed emphasis, buttons) · `24` (panel titles, Silkscreen) · `32` (the
headline metric, Silkscreen) · `48` (stamp text, Silkscreen, ~2 uses total).

Rules: no italics (breaks the pixel register); emphasis = `--work` color or
weight 600, never both; tabular numerals everywhere numbers align (Plex Mono is
already fixed-width — never swap in a proportional face for data).

---

## 4. The garage scene (Pixi.js)

### 4.1 Canvas & grid

- Logical stage **960 × 540** (16:9), integer-scaled to fit; `roundPixels: true`,
  no antialiasing, nearest-neighbor on all textures. Pixel art is either crisp
  or wrong.
- Tile grid **32 × 32** → 30 × 17 tiles. All props snap to the grid; characters
  move on waypoint paths (ADR-9), positions rounded to whole pixels every frame.
- Camera: fixed, whole floor visible. No zoom/pan in v1 — the floor is small
  and legibility beats cinematics.

### 4.2 Floor plan

```
┌────────────────────────────────────────────────────────────┐
│  ▒ garage door (closed, slatted) ▒▒▒▒▒▒▒   ~ neon sign ~   │
│                                                            │
│  [WHITEBOARD]                            [FILING BOXES]    │
│   orchestr.                                  scout         │
│      🧍                                        🧍           │
│                                                            │
│              ┌─────────────┐                               │
│              │ BUILD DESK  │        [TEST BENCH]           │
│              │  ☰ ☰ ☰ 3mon │            tester             │
│              │     🧍      │              🧍                │
│              └─────────────┘        [WORKBENCH]            │
│                                       reviewer             │
│   [COUCH ~ pizza box]                    🧍                 │
│    idle · idle · idle           [SIDE DESK] scribe         │
│                                                            │
│  · cable ·· cable ····· floor tape ····· cable ·           │
└────────────────────────────────────────────────────────────┘
```

Stations (per PRD §5.1) and their **identifying prop** — each station must be
recognizable in silhouette alone:

| Station | Agent | Identifying prop | Working animation |
|---|---|---|---|
| Whiteboard | Orchestrator | Board with a hand-drawn **loop diagram** (literally the PRD flowchart, in chalk pixels) | Draws/taps the board; arrows on the board light per routing decision |
| Filing boxes | Scout | Cardboard boxes + a hanging flashlight | Digs; papers flutter out; flashlight cone sweeps |
| Build desk | Builder | Three monitors (the only lit screens in the room) | Typing; monitors flicker `--wire` scanlines |
| Test bench | Tester | Bench + oscilloscope + the **STAMP** | Runs gauge needle; then the stamp event (§5.2) |
| Workbench | Reviewer | Pegboard of tools + a **ladder leaning on the wall** (the ponytail ladder, visual pun intended) | Holds patch paper up to the lamp, squints |
| Side desk | Scribe | Typewriter + coffee | Types; page scrolls up |
| Couch | idle pool | Sagging couch, pizza box, floor lamp OFF | Sit/blink/occasional phone-glow |

Ambient layer (subtle, ≤ 3 concurrent effects, all disabled under
`prefers-reduced-motion`): neon sign flicker (every ~20s), a moth around the
sodium lamp, monitor scanline drift. Nothing ambient may use `--pass`/`--fail`.

### 4.3 Characters

- 6 sprites, ~24 × 32 px, one shared silhouette set, differentiated by **palette
  swap + one prop** (orchestrator: marker behind ear · scout: headlamp · builder:
  hoodie up · tester: safety glasses · reviewer: rolled sleeves · scribe: scarf).
  Kenney.nl CC0 bases; recolors stay CC0.
- Name tags: 12px Silkscreen, `--paper-dim`, shown always (interviewers won't
  guess who's who).
- States: `idle` (couch, desaturated toward `--ink-3`) → `walking` (4-frame
  cycle along waypoints, 90 px/s) → `working` (station loop + `--work` under-glow)
  → `handoff` (paper sprite arcs station-to-station, 400ms, slight overshoot).

### 4.4 Event → scene mapping (the contract with `events.jsonl`)

The scene is a pure view of the reducer's `SceneState` (TAD §5.2). Full mapping:

| Event | Scene response |
|---|---|
| `task_started` | Issue title chalks onto the whiteboard; lights dim 10% then restore (a "new job" beat) |
| `agent_activated` | Avatar walks couch→station; station lamp turns `--work`; previous `--work` glow extinguishes (§2.2 singular rule) |
| `context_pack_ready` | Scout tosses a paper bundle to the build desk (handoff arc) |
| `patch_produced` | Builder's monitors flash; paper bundle arcs to test bench |
| `tests_run` | Bench gauge needle sweeps for the duration (min 600ms even if grading returned instantly — legibility beats literal timing) |
| `gate_verdict` pass | **STAMP**: green stamp slams (§5.2), `--pass` |
| `gate_verdict` fail / review reject | **STAMP** red; rejection reason renders as a paper note that arcs *back* to the build desk |
| `retry` | Attempt counter on the whiteboard ticks (tally marks, chalk style) |
| `shipped` | Paper flies out through the mail slot in the garage door; brief `--pass` wash on the neon sign |
| `task_failed` | Paper crumples into the bin by the couch; failure type chalked on the whiteboard in `--fail` |
| `budget_exceeded` | The sodium lamp physically dims to 40%; HUD spend counter pulses |
| `cost_tick` | HUD only — the scene never reacts to money except the above |
| unknown type | Ignored (schema v:1 forward-compat rule) |

### 4.5 The signature element: the stamp

One boldness budget, spent here. On every `gate_verdict`:

1. Time freezes 120ms (all other tweens pause — the world waits for the gate)
2. Stamp sprite drops onto the patch paper, 1-frame squash, **2px screen shake**
3. Ink spreads: `PASS` in `--pass` or `FAIL/REJECT` in `--fail`, 48px Silkscreen,
   rotated −6°
4. Sound (off by default, toggle in HUD): dry thunk, 60ms

This is the only screen shake, the only time-freeze, and (with ship/fail) one of
the only three uses of verdict colors in motion. If someone remembers one frame
of this project, it's the stamp.

---

## 5. UI chrome (React, around the canvas)

### 5.1 Layout

```
┌──────────────────────────────────────────────────────────────┐
│ ⌂ THE GARAGE      run r_…a3f2   ● live        [feed][garage] │  top bar 48px
├──────────────────────────────────────────────┬───────────────┤
│                                              │  TASK          │
│                                              │  django-11099  │
│                garage canvas                 │  attempt 2/4   │
│                 (960×540 scaled)             │  ────────────  │
│                                              │  GATES         │
│                                              │  tests   ✗ ✗ · │
│                                              │  review    ·   │
│                                              │  ────────────  │
│                                              │  SPEND         │
│                                              │  $0.041 ▁▂▂▃   │
├──────────────────────────────────────────────┴───────────────┤
│ 14:03:22 tester    gate_verdict tests=fail attempt=2         │  feed drawer
│ 14:03:22 builder   agent_activated (retry 2/3)               │  (collapsible,
│ 14:03:29 builder   patch_produced 41 lines                   │   monospace)
└──────────────────────────────────────────────────────────────┘
```

- **Top bar:** Silkscreen title, run id (Plex Mono, click-to-copy), connection
  dot (`--wire` = live WS, `--paper-dim` = replaying, `--fail` only if the
  server itself is unreachable — infra, not verdict, so justify: this is the
  one sanctioned non-verdict red, rendered as an outlined ⚠ badge, not a fill).
- **Right rail (HUD):** the whiteboard's digital twin. Gate history as stamp
  glyphs (✓/✗/·). Spend in real dollars, 3 decimals, `--work`, with a `--wire`
  sparkline. No cards, no shadows — 1px `--ink-3` rules between sections.
- **Feed drawer:** the week-3 text feed never dies; it becomes the drawer.
  Row format fixed: `time  agent(9ch)  type  key=value…`. Verdict rows tint
  their text (not background) with `--pass`/`--fail`. Click a row → artifact
  panel (lazy REST fetch per TAD §4.1).
- **Feed-only mode** (weeks 3→4 and forever after): the `[feed]` toggle shows
  the drawer full-height. The demo works with the garage OFF — honesty rule.

### 5.2 Replay controls (later phase)

Scrubber styled as **tape**, not a media player: a `--wire` playhead over a
strip that renders event density as tick marks; stamps appear as green/red
notches on the tape (jump-to-verdict is one click). Buttons: `⏮ ⏯ 1x 4x 16x`,
Plex Mono, outlined. During replay the top-bar dot goes `--paper-dim` and the
neon sign in-scene reads `REPLAY` — scene and chrome always agree about liveness.

Hover any agent during replay → tooltip card (`--ink-2`, 1px `--ink-3` border):
role, current event, and `view prompt →` linking the artifact panel (FR-28).

### 5.3 Empty, loading, error states

- No runs yet: dark stage, neon sign OFF, couch only, line of chalk on the
  floor: `uv run garage run-one --task django__django-11099` (copy button).
  The empty state teaches the first command.
- WS reconnecting: feed prints `reconnecting… replaying from seq N` as a normal
  feed row — infrastructure speaks in the same voice as everything else.
- Crashed task: no drama in chrome; the scene's bin + whiteboard handle it,
  the feed shows the trace pointer.

---

## 6. Motion rules

- Durations: micro 120ms · handoff arc 400ms · walk = distance/90px·s⁻¹ ·
  stamp sequence 600ms total. Easing `cubic-bezier(0.2, 0.8, 0.2, 1)` except
  the stamp drop (ease-in, it's gravity).
- Real time wins: animations never queue more than 2 deep; if events outpace
  motion, skip to latest state (the reducer is truth; animation is garnish).
- `prefers-reduced-motion`: no ambient layer, no shake, walks become fades,
  the stamp becomes an instant ink appear. Verdict information survives 100%.
- Nothing animates on a loop faster than 800ms except the working-state cycles.

---

## 7. Docs & README visual identity

The repo's face uses the same tokens: dark hero (`--ink-0`) with the neon-sign
wordmark (Silkscreen, `--work` glow — the one gradient), the results table
**first, screenshot second** (the number before the pixels, rules.md §0.1),
badges in palette colors only. The headline metric is typeset like the HUD:
Plex Mono, counts beside percentages: `17/50 → 26/50 (gate on)`. Any chart in
the README derives from these tokens (matplotlib style file `docs/garage.mplstyle`
committed with the repo).

---

## 8. Asset pipeline & licensing

- Sources: **Kenney.nl CC0 only** (base characters, furniture, tiles). Every
  file's origin logged in `web/src/garage/assets/ATTRIBUTION.md` — pack name,
  URL, license, modifications. CC0 doesn't require it; the interview does.
- Recolors/edits via Aseprite (or LibreSprite); `.aseprite` sources committed
  next to exported sheets.
- Export: PNG sprite sheets + JSON atlases (TexturePacker-free: Aseprite CLI
  in `npm run assets`). Max atlas 1024×1024. No runtime scaling of source art —
  draw at 1×, integer-scale the stage.
- Hard bans re-stated from rules.md: no LimeZu, no Office-referencing props or
  character likenesses, no CC-BY-NC anything.

---

## 9. Accessibility & quality floor

- Verdicts never rely on color alone: stamp *text* says PASS/FAIL, feed rows
  carry ✓/✗ glyphs, gate history uses shapes.
- All chrome keyboard-reachable; visible focus ring = 2px `--work` outline.
- The canvas has an aria-live region mirroring the last event line (the feed
  is the accessible garage).
- Contrast per §2.2; hit targets ≥ 32px; feed rows announce agent + type + verdict.

---

## 10. Definition of visually done (per component)

1. Every color/type/spacing value traces to a token in this file
2. Verdict colors appear only where §2.2 allows
3. At most one `--work` glow on screen
4. Works in feed-only mode; canvas is enhancement, not requirement
5. `prefers-reduced-motion` path verified
6. Asset origins logged in ATTRIBUTION.md

---

*The stamp is the brand. When in doubt, make the verdict clearer and everything
else quieter.*
