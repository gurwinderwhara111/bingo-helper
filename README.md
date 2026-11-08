# Bingo Helper

Watches the screen, reads the called ball(s), tracks your daubs, and dims the
5x5 card except the matching cell. It also gives a Pick Me Up recommendation.

Local only. No input injection, no memory reading, no DLL injection, no network.
The helper never clicks or daubs for you.

## Version 1.1

New in v1.1:

- **Daub tracking.** The helper remembers which cells are already daubed and
  never tells you to pick a number that is already covered.
- **Bingo line engine.** Rows, columns, both diagonals, and the four corners
  (13 lines total, matching the game's help screen).
- **Instant Ball.** When 2-3 large balls appear together, it reads all of them
  and opens all matching cells at once. No second confirming read, because
  those balls disappear quickly.
- **Pick Me Up.** Reads the four offered balls, simulates each one against every
  bingo line, and shows the best choice in the status bar. Ties, unreadable
  balls, or an uncertain daub state show **choose manually** instead of guessing.
- **Extra free / wild cells.** A card with a second pumpkin (or other free
  artwork) is supported; validation allows the extra wild cell.
- **Mid-round start.** Already-daubed cells are read from the screen when the
  helper starts. If the colour is ambiguous, Pick Me Up asks you to choose
  manually for that round.

v1.0 was manually validated in the live game. v1.1 is validated offline against
the reference screenshots and unit tests; run one live round with the console
open (`start.bat`) before relying on Pick Me Up timing.

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

**Instant Ball** — the helper reads every large ball it can see and opens all
matching cells together. If one ball is unreadable it still opens the rest; a
missed ball is better than a wrong one. You still click/daub the cells.

**Pick Me Up** — the status bar shows the best offered number, for example
`PICK ME UP | PICK 44 -> 1 BINGO`. The full ranking is printed to the log.
It shows `choose manually` when:

- fewer than four balls could be read,
- two choices score exactly the same,
- a just-called cell has not been visually confirmed as daubed and would change
  the answer,
- the daub state could not be read clearly.

The helper does **not** activate the powerup and does **not** pick for you.

## The control bar

A small bar sits above the taskbar (drag it anywhere):

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

`run_one.py` is the older one-image debugging tool.
`power up types with images examples\` contains the high-resolution Instant Ball
and Pick Me Up reference screenshots used by the tests.

## Honest limits

- **The launch click is real.** The game pauses whenever it loses foreground
  focus. Launching from a terminal or Explorer costs one click back. The
  `Ctrl+Alt+B` shortcut avoids that; there is no clean way to avoid it otherwise
  without injecting input, which this tool refuses to do.
- **Pause pauses the helper, not the game.**
- **v1.1 is offline-validated** against the provided screenshots and tests. Do
  one live round with `start.bat` first. If the game changes skin, powerup
  artwork, or layout, send a screenshot and it can be tuned.
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
| `run_one.py` | Offline single-screenshot tester |
| `start_silent.vbs` | No-console launcher (daily use) |
| `start.bat` | Console launcher (debugging) |
| `install_shortcut.ps1` | Creates the `Ctrl+Alt+B` Desktop shortcut |
| `bingo.log` | Runtime log (created automatically) |
| `bar_pos.txt` | Saved control-bar position (created after dragging) |
| `Screenshot ...png` | Normal-card reference |
| `power up types with images examples\` | Instant Ball / Pick Me Up references |
