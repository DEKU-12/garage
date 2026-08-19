# Build journal

A plain-English record of what got built each week, what broke, and why we
made the calls we made. No jargon. If you read one file six months from now to
remember how this project went, read this one.

The other docs answer different questions: `PRD.md` says *what* to build,
`TAD.md` says *how it's shaped*, `rules.md` says *what not to do*, `DESIGN.md`
says *what it looks like*. This file says **what actually happened**.

> **How to keep this file:** add one section per week, when that week's exit
> criterion is genuinely met. Write it for a reader who doesn't know the
> codebase. Every bug gets four parts: what it looked like, what was really
> wrong, how we fixed it, and why it mattered. Numbers here come from real
> runs, never estimated (rules.md §0.3).

---

## Week 1 — The harness

**Goal:** one real bug goes in, a fix comes out, tests judge it, and the result
is written down. No AI teamwork yet, no visuals. Just the machinery.

**Status: done.** All six requirements met and verified. 34 tests passing.

### What we built, and why in this order

**1. The referee first.**

The referee is the part that decides "did this fix actually work?" It downloads
the real broken project, applies a fix, and runs that project's own tests
inside a sealed container.

We built it before the AI on purpose. If you build the AI first and it scores
zero, you have no way of knowing whether the AI is bad or your scoreboard is
broken. Get the scoreboard trustworthy first, and every number after it means
something.

**2. Then we tested the referee against answers we already knew.**

Every bug in this benchmark comes with the actual fix a human developer wrote
years ago. So we fed the referee that real fix and demanded a **pass**. Then we
fed it nothing at all and demanded a **fail**. Both came back correct.

This is the single most valuable decision of the week. When you feed in an
answer you *know* is right, any failure is unambiguously **your** bug, not the
AI's. It caught a disaster later that day (bug 3 below).

Result: the real fix passes in ~30 seconds — 3 previously-failing tests flip to
passing, and 19 already-passing tests stay passing. An empty fix fails in 3
seconds.

**3. Then a clean workspace for every attempt.**

The AI gets 4 tries per bug. Each try needs a fresh, untouched copy of the
project, or try 3 is building on top of try 2's mess and you can't tell what
caused what.

Downloading the project each time is too slow — Django is 293 MB and takes 21
seconds. So we download it **once** and hand out cheap copies from it. Each
copy takes **1.2 seconds**. That ratio is what makes "clean slate every time"
affordable instead of a luxury.

**4. Then the translator.**

An AI asked to fix code doesn't hand you a neat file. It hands you a paragraph
of chat, maybe an example, *then* the actual fix, then more chat. This piece
digs the real fix out, sanity-checks it (does this file even exist? is the
format valid?), applies it, and — when it fails — hands the AI git's exact
complaint so the next try knows what went wrong.

**5. Then a fake AI.**

Canned answers, no internet, free, identical every time. This let us test the
whole chain end-to-end before spending a cent. Three of the week's bugs were
caught here for nothing.

**6. Finally, the real AI.** Which is where things got interesting.

### The bugs

Five real bugs. **Four of them looked exactly like "the AI just isn't good at
this."** None of them were the AI.

---

**Bug 1 — Copies landed in the wrong folder**

*What it looked like:* We asked for a clean copy of the project. The tool said
it worked. The folder was empty.

*What was really wrong:* We told git "put it here," but git was standing in a
different folder at the time. So "here" meant somewhere else — copies were
being buried inside the download cache.

*The fix:* Always use full addresses, never "here."

*Why it mattered:* Nothing downstream can work if the code isn't where you
think it is. Loud and obvious — the easy kind.

---

**Bug 2 — A one-word typo rejected valid fixes**

*What it looked like:* Perfectly good fixes were rejected as malformed.

*What was really wrong:* Our format check only examined the **first line** of
the AI's answer instead of every line. The important part is never on line one,
so it never found it. One missing setting in a search pattern.

*The fix:* Tell it to check every line.

*Why it mattered:* This would have thrown away every correct answer the AI
produced, and the results would have read "the AI can't format a fix."

---

**Bug 3 — The silent zero (the dangerous one)**

*What it looked like:* Nothing. It looked completely fine. That's the problem.

*What was really wrong:* Git has two places a change can sit — the working
files, and a "ready to commit" holding area. One way of applying a change puts
it in the holding area. And the normal command for asking *"what changed?"*
doesn't look in that holding area — so it answers **"nothing."**

The file was genuinely fixed. Git knew it was fixed. But we'd have asked the
wrong way, been told nothing changed, and submitted an **empty** fix.

*The fix:* Ask the question the way that sees both places. There's now a test
locking it in.

*Why it mattered:* The referee scores an empty submission as "no fix
submitted → fail." Which is **exactly** what a genuinely failing AI looks like.
Every bug the AI actually solved would have been recorded as a failure. Your
headline result — the entire point of this project — would have been wrong, and
nothing anywhere would have looked broken.

Caught only because we tested with a fix we knew was correct.

---

**Bug 4 — The AI's thinking ate its own answer**

*What it looked like:* The AI returned a completely empty response, four times
in a row, while the bill said it had used a full 2048 words each time.

*What was really wrong:* The models we're using (`gpt-oss`) **think before they
answer**, and they report that thinking separately from the answer. The default
allowance was 2048 words total. On a real bug report, the model used all 2048
thinking — and fell silent before writing a single word of actual answer.

*The fix:* Give it a bigger allowance, tell it to think briefly (we want a code
fix, not an essay), and record *why* each response ended. If a response comes
back empty because it ran out of room, we now say so immediately instead of
retrying the identical request three more times.

*Why it mattered:* Four wasted attempts per run, and the results blamed the AI
for a setting we'd never configured.

---

**Bug 5 — Asking for more than the whole minute's budget**

*What it looked like:* Every single request rejected before the AI even read
the question.

*What was really wrong:* The free plan allows 8000 words per minute — and it
counts the *room you reserve for an answer* against that limit. We'd reserved
8192. Over budget before saying hello.

*The fix:* Reserve 6000, leaving room for the question. Also: "your request is
too large" is now treated as permanent rather than retried, because sending the
identical oversized request again can't possibly work.

*Why it mattered:* Beyond the immediate breakage, this sets a hard ceiling for
next week. The plan calls for giving the AI 6000 words of code context —
**that won't fit** alongside a question and an answer on the free tier. Week 2
either uses a smaller context or the account gets upgraded.

---

**Near-miss — the API key nearly went public**

There are two similarly-named files: `.env` (private, ignored by git) and
`.env.example` (a blank template that ships publicly with the code). The key
went into the template.

Caught before any commit — checked, and it had never entered the project's
history. Moved to the private file. Worth remembering: automated bots scan
public code for exposed keys within minutes, and once a key is in a project's
history, deleting it later doesn't help. Revoking it is the only real fix.

### Why we didn't do the obvious alternatives

**Why not build the visualization first?** It's the fun part and the part
interviewers see. But a beautiful animation of a system that reports a *wrong*
number is worthless. Bug 3 would have been animated in full colour. The number
comes before the pixels.

**Why not write our own test runner?** Each of these bug reports needs its own
specific, dated software environment. Building that yourself is the most
reliable way to lose a month. The official runner already exists — and using it
means your results are directly comparable to everyone else's.

**Why not use an off-the-shelf agent framework?** Because the hand-built version
*is* what you're demonstrating. When someone asks "why three retries and not
five?", you need to answer from code you wrote. A framework answers "it just
does that."

**Why a fake AI before the real one?** Free, instant, identical every time.
Three bugs caught for nothing. Debugging those against a paid model would have
been slow and confusing — you'd never know if a failure was your code or the
model having an off day.

### Where week 1 ended

The plumbing works. The real AI runs, responds, and is correctly judged.

It solved **nothing** — and that is the expected, useful result.

Here's why it failed. It was guessing at a fix having never been shown the
project. It said the file lived at `contrib/auth/validators.py`. It actually
lives at `django/contrib/auth/validators.py`. It never saw the folder layout,
so it guessed and missed. Then it guessed wrong again on its fourth try, in
exactly the same way, despite being told the first time.

That's not a flaw. **That's the baseline** — the "before" half of the result
this whole project exists to produce.

One real run, for the record:

| | |
|---|---|
| Attempts | 4 of 4 used |
| Outcome | no fix ever applied cleanly |
| Words used | 2616 in, 2199 out |
| Time | 103 seconds |
| Response speed | 0.8s to 50s (free tier is unpredictable) |

**Two things week 2 must deal with:** the AI can't see the project, and the
feedback we send it isn't changing its behaviour — try 4 repeated try 1's exact
mistake after being corrected.

---

## Week 3 — The event stream

*Not started. This section gets written when the week-3 exit criterion is met:
a live run streams to a browser text feed, and a saved run replays as text.*

## Week 2 — the loop, and the number

**Goal:** wire the agents into a real state machine with quality gates, run 30
bugs twice — once with the gate on, once off — and find out what the gate is
actually worth.

**Status: done.** The number exists.

### The result

| | Solved | $/solved |
|---|---|---|
| Gate **off** (one shot) | 9 of 26 | $0.17 |
| Gate **on** (retries) | **13 of 26** | $0.46 |

**+15 points of solve rate, for about 2.8x the cost per fix.**

The headline undersells the mechanism. Count the tasks where the AI never
managed to write a *valid* fix at all — not a wrong fix, an unusable one:

| | |
|---|---|
| Without second chances | **14** |
| With second chances | **7** |

Halved. That is the entire loop in one number: when a patch fails to apply, git
says exactly what is wrong, that text goes back to the AI, and the second
attempt fixes it. Four of those recovered went on to pass their tests.

### What we built

The state machine, the two gates, the batch runner, cost accounting, and the
report generator that produces every number above from raw logs.

The design decision worth defending in an interview: **turning a gate off
rewires the machine rather than adding an if-statement.** With the gate off,
there is physically no path back to the builder — it cannot retry even in
principle. A test inspects the compiled graph to prove it. That is what makes
the comparison honest instead of a promise.

### The bugs — nine of them, and a pattern

Week 1 had five bugs that looked like the AI failing. Week 2 had four more, and
they rhyme: **every one made something look like a model failure when it was
ours, and several would have put a wrong number in front of a reader.**

**1. Retries were off by one, everywhere.** We counted *failures seen* as
*retries taken*. But the first failure hasn't been retried yet — so every gate
lost an attempt. The simplicity reviewer was the worst case: it gave up on its
*first* rejection, never once asking for a smaller patch. That gate would have
measured as worthless because it never actually fired.

**2. A repair silently vanished, and 58 tests stayed green.** Models write
`@@` without the line numbers that follow it — the edit is right, the
arithmetic is missing. We compute it for them. During a refactor that step got
dropped, and the entire test suite still passed, because every test used a
well-formed example. Only a real run caught it. There is now a deliberately
malformed one in the fixtures.

**3. The Scout was looking in the wrong place.** It ranked files by how many
search terms they matched. A bug report mentioning `FilePathField` (the class
being fixed, a handful of matches) and `CharField` (its parent, hundreds) would
send it to CharField's neighbourhood — and the class being fixed never made it
into what the AI saw. The AI said so in plain words: *"I don't have enough
information about the exact location and surrounding code."* Rare words now
count for far more than common ones, and defining a name beats mentioning it.

**4. Running out of daily quota was recorded as the AI failing.** Groq's free
tier caps tokens per day. When it ran out, 43 calls came back rejected — the AI
never saw them — and each was filed as a model failure. The result read
"2/30 both arms, no lift," and the table rendered perfectly. The arithmetic was
real; the rows were lies. **The only visible tell was the clock**: 60 runs
finished in 25 minutes when one run takes 1.7. Quota exhaustion now stops the
batch and writes *nothing* for the task in flight, so a resume re-runs it
instead of inheriting a failure that never happened.

### The two that cost real money

**5. A short timeout was double-paying for the same work.** Our request timeout
was 120 seconds. Real generations ran to 237. When the client gives up, the
provider has *already produced the tokens and charges for them* — then the SDK
quietly re-sends, on top of our own retry loop. One logical call could bill up
to nine generations while our ledger recorded one.

We only found it because the bill and the ledger disagreed: **$5.44 charged
against $2.20 recorded.** Our own accounting was perfectly self-consistent and
completely wrong, because it can only see responses that arrive. A client-side
ledger is structurally blind to work you abandon.

Fixed by not abandoning work: timeout raised to 900s, and the SDK's own retries
turned off so there is exactly one retry policy. Verified against the provider's
usage page: predicted 813,696 in / 465,094 out, actual **813,696 / 465,094** —
exact to the token.

**6. Our own safety caps were rigging the experiment.** Twice. A token cap and
then a dollar cap, both sized for the previous provider, cut the gate-on arm's
final attempt — and *only* the gate-on arm can reach a cap, because only it
retries. Both made the gate look worse than it is. The first was caught and
fixed mid-experiment; the second survived into the final run, which is why the
published +15 is labelled a **lower bound** rather than a result.

**A backstop that binds before the thing it is backing up has silently become
the limit.** There is now a test asserting the cap clears the retry budget.

### Two more, briefly

**7. A fix that didn't apply, in a commit that said it did.** A find-and-replace
whose search text didn't match: the file was unchanged, the commit message
confidently described the change. An assertion written in that same commit
failed and caught it — the argument for testing settings rather than trusting
that an edit landed.

**8. Case-insensitive filesystems.** Launching with `--run-id E1` silently
resumed a quarantined run named `e1` — macOS treats them as the same directory.
It skipped all 30 tasks, exited 0, and printed a report headed with the new
model containing the old model's numbers. The collision was the trigger; the
real defect was that resume never checked whether the existing run matched the
requested configuration. It now refuses on any mismatch.

### What it cost

About **$8** and roughly two hours of compute for the final run — plus perhaps
$3 wasted on the double-billing before it was found, and four abandoned runs
along the way.

Worth noting: **the entire harness was built and debugged on a free tier.**
Every bug above was found for $0. Switching to a paid model happened only when
the free tier's daily quota turned a two-hour experiment into a three-day queue.
Debug for free; measure for money.

### What we deliberately did not do

Fix the caps and re-run for a prettier number. The +15 is measured under
conditions that penalise the gate, and it is reported that way, with the reason
stated. A smaller honest number beats a larger tuned one — and the rules of this
repo forbid the tuning anyway.

### Where week 2 leaves things

The engine works and the number is real. `experiments/E1_gate/` holds the frozen
configs, the raw rows, and the report — which is what unlocks the visualization
work in weeks 3 and 4 under this repo's own first law: the number comes before
the pixels.

Two things to fix before E2 and E3, since both would distort those experiments
the same way: raise the per-task dollar cap above what a full retry budget
costs, and raise the output ceiling so hard tasks stop truncating mid-diff.
