# Bingo Helper

Watches the screen, reads the called ball(s), tracks your daubs, and dims the
5x5 card except the matching cell. It also gives a Pick Me Up recommendation.

Local only. No input injection, no memory reading, no DLL injection, no network.
The helper never clicks or daubs for you.

## Version 1.14

v1.14 is a reliability overhaul of the ball pipeline, driven by a poll-by-poll
production trace on both replay videos (tools/_trace_tail.py):

- **Settled-frame confirmation.** Both reads of the two-read confirm must come
  from a frame where the ball's own pixels stopped moving; the diff gate now
  uses max(whole-crop, hot-zone) so a same-slot ball swap is always seen; the
  instant path requires each box settled before reading. This killed every
  pixel-verified phantom (B4/B3/B7/B1/N34, the B2/N34 sliding-ball garbage at
  37.10s, B5, B8) with zero real calls lost.
- **Landing-bounce ratification.** A ball that lands between two polls reads as
  "moving" one poll too long, and at a round's end that burned the whole
  confirm window (I26 play1 146.8s, O67 play2 152.9s: three agreeing 1.00
  reads each, never committed). A third identical read whose Hough box stopped
  moving (docked jitter <=2px/poll vs 10-45px/poll in flight) now ratifies, so
  both videos commit every readable ball including the last one before
  TIME'S UP.
- **Letter/number contradiction check** in the normal path (contradiction-only;
  an empty letter band passes).
- **Dual-read policy unchanged**: OCR primary, template bank agreement-only.

Gates (tools/compare_events.py): the 7 (play1) and 5 (play2) calls lost to v13
are all pixel-verified phantoms or off-card announce-only diffs; nothing
gained; daubs identical (13/13, 14/14); v14d -> v14e adds exactly I26@146.84
and O67@152.92. Regressions: test_v11 19/19, test_v12 7/7.

## Version 1.13

v1.13 fixes the last false-highlight source found after v1.12: the
current-ball selector. A live-log forensics pass plus a 28-screenshot
accuracy audit on the user-collected corpus (human-verified ground truth,
every screenshot read by eye) showed the OCR itself was right 117/118 -
but box selection read the seated cat instead of the ball in 18/28 files:

- **Validated current-ball selection.** `V.select_current()` replaces
  "largest candidate wins": hot zone (`cx >= grid.x0 + 0.60*width`) plus
  the drawn-ball size band (`[0.80, 1.35]` of the card cell), rightmost
  box. On the labelled corpus: old rule 10/28, new rule 28/28, stable at
  every zone width 0.50-0.65. Ring/face identity probes were tested and
  rejected on measurement (real 42px history balls measure as low as
  0.006 saturated-ring fraction while cat phantoms still reach 0.42).
- **Multi-ball frames use the same rule.** `poll_ball` picked the biggest
  of the `find_balls` boxes, re-introducing the same bug; it now calls
  `select_current`. `find_balls` itself clusters duplicate circles by
  centre proximity (keeps the typical-size box) and stays inside the size
  band, so one over-sized blob can no longer poison the radius ratio.
- **Measured A/B on both replay videos** (tools/test_ballpick.py,
  debug/eval/v12_events_* vs v13_events_*): +5 real balls the old rule
  never read, -4 cat-blob fragment calls ("B8"->"B6", "B9"->"B7"
  split-reads), earlier reads (up to 11.6s), no new wrong highlights.
- **Corpus accuracy harness.** tools/eval_screenshots.py scores every
  screenshot against human labels (debug/eval/truth.json): grid 672/672
  cells (100%), ball reads 117/118 (the single miss is a missing circle,
  not a misread), 28/28 valid cards.
- **Training corpus committed.** The 28 screenshots + verified ball/card
  labels are the corpus the bank rebuild will use next; 19 of the 32 seen
  numbers still have no template row. The interim video-built bank
  (assets/ball_templates.npz) is coverage-limited and does NOT gate
  commits - OCR carries the reads.

Regressions: test_v11 19/19, test_v12 7/7; replay baselines re-verified
(play1 52->54, play2 53->61 events; every diff explained above).

## Version 1.12

v1.12 fixes the three reliability complaints from live play: normal calls
being dropped while a power-up popup was open, silent normal-play ball misses,
and wrong-daub advice. Backed by a live-log forensics pass, a web fact-check
of WorldWinner rules, and an internal simulation of all 75 ball values:

- **Ball reads keep running during Pick Me Up.** The WorldWinner clock keeps
  running while the popup is open, so normal calls arriving in that window
  are real. `poll_ball` no longer hard-returns while a popup is known; it
  only skips frames whose modal title band physically covers the ball strip.
  Commits during a popup are safe: they locate against the saved card, and
  highlights revive when the card reappears.
- **The uncertain-daub advice is removed.** Wrong daubs cost -100 points and
  most NOT-ON-CARD calls are genuinely not on the card, so guiding a daub on
  an unreadable cell was negative-value advice (complaint: it showed
  "choose this next daub" instead of the ball).
- **Gone-forever misses fixed.** `commit()` keyed every ball into
  `last_calls` before knowing whether the card covers it, so a ball wrongly
  scored NOT ON CARD against a bad card could never highlight after the card
  healed. Missed keys are now remembered (`not_on_card`) and released the
  moment any card correction lands (`_retry_missed`), re-committing and
  lighting the call.
- **Powerup offers print once.** The floating 3-ball Instant offers that are
  not on the card were re-read and re-printed every 120 ms (live log showed
  `instant G 52 -> NOT ON CARD` seven times). Offer keys now dedupe for the
  rest of the window and announce as `powerup offer ... - not on card`.
- **No silent misses.** Reads below `BALL_SCORE` used to vanish without a
  trace. They now log throttled (`ball unread (score 0.83) - retrying`,
  `instant ball unread ...`) so every miss is diagnosable in `bingo.log`.
- **Multi-ball frames pick the biggest ball.** The current ball is the
  largest on screen; the old right-most rule could lose it.
- **All-75 verification harness.** `tools/simulate_numbers.py` renders every
  bingo value 1..75 (game style, five grid widths, clean and fully daubed)
  and reads them back with the production readers: the OCR fallback path
  reads 75/75 at every width; the ball readers read 75/75 at every size
  40-64 px. Plus 7 new app-level regression tests (test_v12.py) covering the
  popup gate, the heal-and-recommit path, and offer bookkeeping.

## Version 1.11

v1.11 fixes the failure chain found in the live session against
web.worldwinner.com (the HTML5 game site), where the helper locked a new
ticket with five numbers from the *previous* ticket grafted in, then missed
`G 60`, `N 41` (three times) and flashed wrong holes for `O 65` and `N 40`:

- **Round switches never reuse locked daub values.** v1.10's daub-aware
  re-read replayed the previous ticket's daubed values into every new card
  read, and both confirming reads replayed them identically, so a new ticket
  locked corrupted. A switch is now only decided by a fresh-eyes re-read
  (no value injection) that validates **and** has at least 23 readable cells.
- **Missed calls heal the card.** A `NOT ON CARD` call now triggers one
  rate-limited fresh re-read; up to three disagreeing cells are corrected
  in place (log: `card corrected (miss re-read): ...`).
- **Unknown-daub cells guide missed calls.** If exactly one daubed cell in
  the ball's column has no readable value, the hole is shown there with an
  "uncertain" label instead of nothing.
- **Small same-ticket diffs correct instead of resetting.** Fewer than 12
  differing cells on a confirmed fresh read patch the values in place and
  keep the live round (`card corrected: ...`); only big changes declare a
  new round.
- **Initial scans require two agreeing reads** before locking, and grids
  narrower than 300 px get a "maximize the window" warning because small
  cells multiply OCR errors.

## Version 1.10

v1.10 fixes the top live-play failures found while fact-checking the HTML5
clone review against `bingo.log` and the gameplay videos:

- **`int()` digit crash fixed.** RapidOCR can return unicode digits such as
  `'²'`, `'¹'` or `'⑤'`; those passed `isdigit()` but crashed `int()` (7 hard
  crashes in the log). Only ASCII decimal digits are accepted now, in both the
  card reader and the ball reader.
- **Daubed cells never become "free".** When the card is re-read mid-round,
  pixel-confirmed daubed cells reuse their locked number and skip redeciding,
  so an orange daub fill can no longer trigger fake "new round" wipes. Re-reads
  are also faster because daubed cells skip template matching/OCR entirely.
- **Pick Me Up retries a half-read popup.** The four offered balls pop in one
  by one; instead of locking in "manual" from the first frame, the popup is
  re-read every ~0.25 s and only falls back when it closes unread.
- **Stale free/wild ghosts are retracted.** If a later clean read says a cell
  is a number again, the ghost free marking is removed and the daub matrix is
  corrected. 22 ghost events and 13 fake round wipes were in the live log.

## Version 1.9

v1.9 changes normal call handling to prioritize **zero missed guidance**:

- **One reliable read commits immediately.** A high-confidence called ball is
  no longer made to wait for a second polling confirmation.
- **Low-confidence reads retry.** The helper still never invents a number; it
  keeps trying until the ball can be read.
- **No stale auto-hide.** A valid highlight is not removed merely because the
  daub pixel confirmation is slow.
- **Wrong-cell recovery remains.** If a different cell is actually daubed, the
  wrong hole is cleared immediately.

## Version 1.8

v1.8 adds wrong-highlight protection:

- **Half-dealt cards are rejected.** A new-round frame cannot be accepted unless
  the card has enough readable numbers, so a card being dealt is not mistaken
  for a card full of free/wild cells.
- **Unconfirmed highlights clear themselves.** A provisional hole is hidden
  after a short timeout if it is not confirmed as daubed.
- **Wrong-cell recovery clears fast.** If you daub a different cell, the old
  hole is cleared instead of staying on the wrong number.
- **Safer fast-card threshold.** Low-confidence digit matches now fall back to
  OCR instead of being accepted as fact.

## Version 1.7

v1.7 replaces the oversized bar with a professional compact HUD strip:

- **Status left, controls right.** A clear vertical separator splits the live
  status from the buttons.
- **Compact bar body.** No giant bar or oversized buttons.
- **Clean controls.** `Scan`, `Pause`/`Resume`, and `Quit` are normal-sized,
  rounded buttons.
- **Same no-focus behavior.**

## Version 1.6

v1.6 improves the on-screen control bar:

- **Larger, more readable bar.** Bigger status text, larger buttons, full
  `Scan` / `Pause` / `Quit` labels, and a wider status area.
- **Same no-focus behavior.** It still never steals focus from the game.

## Version 1.5

v1.5 fixes Pick Me Up tie behavior:

- **Exact ties now produce a pick instead of "manual".** If two or more offers
  score exactly the same, the helper picks the top tied number and still shows
  the tied numbers, e.g. `tie 44/37 -> PICK 44`.
- **Safety unchanged for real uncertainty.** Manual is still used when fewer
  than four balls are readable, no offer is on the card, or the last daub is
  unconfirmed.

## Version 1.4

v1.4 fixes Instant Ball detection from the real power-up screenshot:

- **Instant Ball now works without relying on the pink text pill.** The real
  power-up can show three large balls directly above the card with no readable
  "INSTANT BALL" banner. The helper now recognizes three large balls aligned in
  one row above the grid.
- **No fake Instant Ball from normal history.** A single current ball or small
  call-history balls do not trigger this path.
- **Reference-image test added** for the actual Instant Ball layout.
- **Real app image test passes:** `G57 O63 G59` are read and all matching
  cells open together.

## Version 1.3

v1.3 keeps v1.2 behavior and focuses on speed and system safety:

- **Fast card reading.** Normal cards are read with digit templates instead
  of running OCR 24 times. On this project's test machine the card read drops
  from roughly **550–890 ms** to **~5–8 ms**.
- **OCR fallback stays.** If a template read is unreadable, out of column
  range, or fails card validation, the helper falls back to RapidOCR rather
  than guessing. A wrong highlight is still worse than a slower one.
- **Cached grid geometry.** The saved grid-crop is diffed first, so the helper
  does not pay full-screen grid detection on every cheap round check. It still
  re-anchors when the card actually moves, hides, or changes.
- **Tests added** for the fast card reader against the normal and Instant
  reference cards.
- **Template rebuild tool.** `tools/build_digit_templates.py` regenerates
  `assets/digit_templates.npz` if the game changes its number font.

## Version 1.2

v1.2 keeps everything in v1.1 and adds recording-based fixes:

- **Instant Ball is confirmed by the game's own banner.** Normal play shows a
  row of small call-history balls above the card. Multiple ball boxes alone no
  longer become Instant Ball. The helper only opens every large ball when the
  pink **INSTANT BALL** marker is present; otherwise it treats the largest/
  right-most ball as one normal call.
- **Offline gameplay replay validation.** `tools/replay_video.py` runs the real
  helper logic over a recorded round with no screen capture, overlay, or input.
  It prints a CSV timeline and event log so mistakes can be reviewed safely.
- **More robust grid detection from real recordings.** Internal row/column
  lines can be hidden by daubs; the grid now infers the missing spacing from
  the card edges instead of giving up, while still rejecting non-square panels.
- **Multiple free/wild cells are supported**, including a whole pumpkin/free
  column when the game deals one. Extra free cells are read visually, not from
  a failed OCR read.

### Version 1.1

v1.1 added:

- **Daub tracking.** The helper remembers which cells are already daubed and
  never tells you to pick a number that is already covered.
- **Bingo line engine.** Rows, columns, both diagonals, and the four corners
  (13 lines total, matching the game's help screen).
- **Instant Ball.** When 2-3 large balls appear together with the game's
  Instant Ball marker, it reads all of them and opens all matching cells at
  once. No second confirming read, because those balls disappear quickly.
- **Pick Me Up.** Reads the four offered balls, simulates each one against every
  bingo line, and shows the best choice in the status bar. Ties, unreadable
  balls, or an uncertain daub state show **choose manually** instead of guessing.
- **Extra free / wild cells.** A card with a second pumpkin (or other free
  artwork) is supported; validation allows the extra wild cell.
- **Mid-round start.** Already-daubed cells are read from the screen when the
  helper starts. If the colour is ambiguous, Pick Me Up asks you to choose
  manually for that round.

v1.1 was manually validated in the live game. v1.2 is validated offline against
the reference screenshots, unit tests, and two gameplay recordings. Still do
one live round with the console open (`start.bat`) before relying on Pick Me Up
timing.

---

## Run it

**Daily use (no console):**

1. Open the game tab so the **whole card is visible** (header + 5x5 grid + the
   ball). Start at the beginning of a round when possible.
2. Double-click **`start_silent.vbs`**.
3. If the game paused, click the game once. The helper retries by itself.

**Start without losing focus (recommended):**

Run `install_shortcut.ps1` once. It puts a **Bingo Helper** shortcut on your
Desktop bound to **`Ctrl+Alt+B`**. Press that while the game is focused, so the
helper starts without taking focus and the game does not pause.

```powershell
powershell -ExecutionPolicy Bypass -File .\install_shortcut.ps1
```

**Debug run (console):**

Double-click **`start.bat`**. Use this when something fails so you can see the
messages; the game will pause until you click it once.

You do **not** need to keep the console open for daily use. The helper logs to
`bingo.log` in this folder.

## What is automatic

- **Ball scanning is continuous.** It does not wait for a keypress per call.
- **The card is locked once per round.** It stores all 25 numbers and their
  screen cells. Calls only move the holes; the card is not re-read every call.
- **Fast card read first, OCR fallback.** The helper tries the digit templates
  first; if that read is invalid it falls back to RapidOCR instead of guessing.
- **Daubs are tracked from card pixels.** A highlight is only a temporary
  marker. The saved daub matrix is what Pick Me Up and bingo counts use.
- **Popups and powerups keep the saved card.** Free Daub, Pick Me Up, Instant
  Ball, boosters, a How-to-Play page, or any popup do not make it forget the
  round. If the card is covered, the dark layer hides and comes back when the
  card does.
- **New round?** Only when the card's numbers actually change. The new read is
  confirmed twice before replacing the old mapping, so one bad OCR read cannot
  cause a wrong highlight.
- **Card moved / resized / zoomed?** A big visual change triggers a re-anchor
  automatically. `Scan` is the manual fallback.
- **Only one copy runs.** Launching it twice does nothing; use the bar to quit.

## Round state (why popups do not break it)

1. Once per round it reads the 5x5 card and stores each number plus the cell
   rectangle on screen.
2. Each called ball is looked up in that stored map. The overlay opens the
   matching cell; the previous one goes back to dark.
3. A cheap check compares the 25 cells with the start-of-round copy. A few
   changed **saturated** cells are daubs. Many changed cells mean the card is
   covered, moved, or has been re-dealt.
4. Covered card → hide the dark layer, keep the saved round, wait.
   Moved card → re-anchor and keep the same numbers.
   New numbers → re-read, confirm, and replace the map.

## Powerups

**Instant Ball** — the helper reads every large ball it can see **only while
the game's pink INSTANT BALL marker is present** and opens all matching cells
together. Normal call-history balls are not Instant Ball. If the marker is
missing, multiple ball boxes are treated as one normal call. If one ball is
unreadable it still opens the readable balls and keeps retrying the shot; the
goal is no missed ball, not silently skipping one.
You still click/daub the cells.

**Pick Me Up** — the status bar shows the best offered number, for example
`PICK ME UP | PICK 44 -> 1 BINGO`. The full ranking is printed to the log.
It shows `choose manually` when:

- fewer than four balls could be read,
- none of the offers are on the card or its cell is already daubed,
- a just-called cell has not been visually confirmed as daubed and would change
  the answer,
- the daub state could not be read clearly.

An exact tie is no longer a failure. The helper picks the top tied number and
shows the tied alternatives, for example `tie 44/37 -> PICK 44`.

The helper does **not** activate the powerup and does **not** pick for you.

## The control bar

A compact, professional control bar sits above the taskbar (drag it anywhere).
The live status is on the left, a separator divides it from the controls, and
the buttons stay normal sized:

| Button | Action |
|--------|--------|
| **Scan** | Re-detect the card now (also works if the window moved) |
| **Pause / Resume** | Pauses **the helper**, not the game. The game keeps running. |
| **X** | Quit the helper |

Clicking the bar **does not take focus**, so the game does not pause. The status
dot shows what is happening: green = scanning/guidance, amber = waiting/paused,
red = card lost/unreadable.

The bar remembers where you dragged it (`bar_pos.txt`).

## Hotkeys (optional fallbacks)

| Key | Action |
|-----|--------|
| `F8` | Re-detect the card now |
| `F9` | Toggle the overlay on/off |
| `F10` | Quit |

## What you will see

- **Number on the card** → the 5x5 grid goes dark, one cell stays clear.
  Bar: `B 13 -> r1c1`. On the next called number, the old cell goes back to dark
  and the new one opens.
- **Number not on the card** → no overlay (nothing is hidden).
  Bar: `B 5 -> not on card`
- **Instant Ball** → all matching cells stay clear at once.
  Bar: `INSTANT BALL | G57 O63 G59`
- **Pick Me Up** → the bar shows the best pick; overlay hides so you can see the
  popup.
- **Popup / powerup covering the card** → the dark layer hides, the saved round
  stays in memory, and the layer returns when the card is visible again.
- **New round** → the changed numbers are re-read and confirmed automatically.
- The B/I/N/G/O header row is **not** darkened; only the 5x5 playing cells are.

## If something is wrong

| Symptom | Fix |
|---------|-----|
| `WAITING FOR CARD` | Card not fully visible, or the game is paused behind its banner. Click the game. |
| Wrong cell highlighted | Click **Scan**. If it persists, send me the screenshot. |
| Overlay not showing | Press `F9`, or click **Scan**. |
| Pick Me Up says choose manually | It is being safe. Check the log; send a screenshot if it happens every time. |
| `could not register F8/F9/F10` | Another copy is running, or another app owns the key. Use the bar to quit the old one. |
| Nothing happens on a call | The game tab must be the visible tab. The tool sees pixels, not the browser. |
| It started but nothing appears | Check `bingo.log` in this folder. |
| The bar is in the way | Drag it. It stays where you put it. |

## Test without the game

Unit + reference-image tests:

```bat
python test_v11.py
```

Single-screenshot tester (normal card):

```bat
python app.py --image "Screenshot 2026-11-06 095328.png" --dry-run --polls 2
```

Offline gameplay replay (real recorded round, local only):

```bat
python tools/replay_video.py "path\to\gameplay.mp4" --fps 4 --start 8 --end 150
```

This decodes the video locally, runs the same vision/round logic with a virtual
clock, and writes `debug/replay_<video>/timeline.csv` plus `events.txt`. It
does not capture your screen, draw an overlay, or click the game.

Rebuild digit templates if the game font changes:

```bat
python tools/build_digit_templates.py
```

`run_one.py` is the older one-image debugging tool.
`power up types with images examples\` contains the high-resolution Instant Ball
and Pick Me Up reference screenshots used by the tests.

## Honest limits

- **The launch click is real.** The game pauses whenever it loses foreground
  focus. Launching from a terminal or Explorer costs one click back. The
  `Ctrl+Alt+B` shortcut avoids that; there is no clean way to avoid it otherwise
  without injecting input, which this tool refuses to do.
- **Pause pauses the helper, not the game.**
- **v1.3 is offline-validated** against the provided screenshots, unit tests,
  and two gameplay recordings. Do one live round with `start.bat` first. If the
  game changes skin, powerup artwork, card font, or layout, rebuild the digit
  templates or send a screenshot.
- **Pick Me Up timing is real-time.** The helper reads the popup as fast as it
  can, but it must stay accurate. When in doubt it asks you to choose manually.
- **75-ball only** (B/I/N/G/O, 1–75). A 90-ball mode would need changes.
- **The ball must be above the card**, as in the screenshots. If the layout
  changes, send a screenshot.
- **No auto-click / no auto-daub.** The helper only highlights and advises. It
  does not press the game. Auto-click remains out of scope unless explicitly
  approved.
- It captures the visible screen with DXGI. Nothing is recorded or saved unless
  you run the test commands.

## Files

| File | Purpose |
|------|---------|
| `app.py` | Live loop: capture, round state, daubs, Instant Ball, Pick Me Up, overlay, bar |
| `bingo.py` | Pure 13-line bingo math and Pick Me Up ranking |
| `vision.py` | Grid, card, ball, popup, free-cell and daub detection, OCR |
| `overlay.py` | Topmost click-through per-pixel-alpha window (one or many holes) |
| `controls.py` | Always-on-top no-focus control bar |
| `test_v11.py` | Unit tests + Instant/Pick Me Up reference-image checks |
| `tools/replay_video.py` | Offline replay of a recorded round for validation |
| `tools/build_digit_templates.py` | Rebuilds the fast digit-template asset |
| `assets/digit_templates.npz` | Digit templates used by the fast card reader |
| `run_one.py` | Offline single-screenshot tester |
| `start_silent.vbs` | No-console launcher (daily use) |
| `start.bat` | Console launcher (debugging) |
| `install_shortcut.ps1` | Creates the `Ctrl+Alt+B` Desktop shortcut |
| `bingo.log` | Runtime log (created automatically) |
| `bar_pos.txt` | Saved control-bar position (created after dragging) |
| `Screenshot ...png` | Normal-card reference |
| `power up types with images examples\` | Instant Ball / Pick Me Up references |
