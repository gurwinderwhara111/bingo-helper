# Bingo Helper

Watches the screen, reads the called ball, and dims the card except the matching cell.

Local only. No input injection, no memory reading, no DLL injection, no network.

---

## Run it

**Daily use (no console):**

1. Open the game tab so the **whole card is visible** (header + 5x5 grid + the ball).
2. Double-click **`start_silent.vbs`**.
3. If the game paused, click the game once. The helper retries by itself.

**Start without losing focus (recommended):**

Run `install_shortcut.ps1` once. It puts a **Bingo Helper** shortcut on your
Desktop bound to **`Ctrl+Alt+B`**. Press that while the game is focused and the
helper starts without taking focus, so the game does not pause.

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
  screen cells. Calls only move the hole; the card is not re-read on every call.
- **Popups and powerups keep the saved card.** Free Daub, Pick Me Up, Instant
  Ball, boosters, a How-to-Play page, or any popup do not make it forget the
  round. If the card is covered, the dark layer hides and comes back when the
  card does.
- **New round?** Only when the card's numbers actually change, it re-reads the
  card. The new read is confirmed twice before replacing the old mapping, so one
  bad OCR read cannot cause a wrong highlight.
- **Card moved / resized / zoomed?** A big visual change triggers a re-anchor
  automatically. `Scan` is the manual fallback.
- **Paused accidentally?** It keeps checking quickly until the card is readable
  again. A popup never throws the saved card away.
- **Only one copy runs.** Launching it twice does nothing; use the bar to quit.

## Round state (why popups don't break it)

1. Once per round it reads the 5x5 card and stores each number plus the cell
   rectangle on screen.
2. Each called ball is looked up in that stored map. The overlay shows one
   clear cell; the previous one goes back to dark.
3. A cheap check compares the 25 cells with the start-of-round copy. A few
   changed cells are normal daubs. Many changed cells mean the card is covered,
   moved, or has been re-dealt.
4. Covered card -> hide the dark layer, keep the saved round, wait.
   Moved card -> re-anchor and keep the same numbers.
   New numbers -> re-read, confirm, and replace the map.

## The control bar

A small bar sits above the taskbar (drag it anywhere):

| Button | Action |
|--------|--------|
| **Scan** | Re-detect the card now (also works if the window moved) |
| **Pause / Resume** | Pauses **the helper**, not the game. The game keeps running. |
| **X** | Quit the helper |

Clicking the bar **does not take focus**, so the game does not pause. The status
dot shows what is happening: green = scanning, amber = waiting/paused, red =
card lost/unreadable.

The bar remembers where you dragged it (`bar_pos.txt`).

## Hotkeys (optional fallbacks)

| Key | Action |
|-----|--------|
| `F8` | Re-detect the card now |
| `F9` | Toggle the overlay on/off |
| `F10` | Quit |

## What you will see

- **Number on the card** → the 5x5 grid goes dark, one cell stays clear.
  Bar: `B 13 -> row 1 col 1`. On the next called number, the old cell goes
  back to dark and the new one opens.
- **Number not on the card** → no overlay (nothing is hidden).
  Bar: `B 5 -> not on card`
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
| `could not register F8/F9/F10` | Another copy is running, or another app owns the key. Use the bar to quit the old one. |
| Nothing happens on a call | The game tab must be the visible tab. The tool sees pixels, not the browser. |
| It started but nothing appears | Check `bingo.log` in this folder. |
| The bar is in the way | Drag it. It stays where you put it. |

## Test without the game

```bat
python app.py --image test_oncard.png --dry-run --polls 4
python app.py --image image.png --dry-run --polls 4
```

`test_oncard.png` is the pumpkin card with the ball changed to `B 13`, which is
at row 1, col 1. It should print `B 13 -> row 1 col 1` and the cell rect.

## Honest limits

- **The launch click is real.** The game pauses whenever it loses foreground
  focus. Launching from a terminal or Explorer costs one click back. The
  `Ctrl+Alt+B` shortcut avoids that; there is no clean way to avoid it otherwise
  without injecting input, which this tool refuses to do.
- **Pause pauses the helper, not the game.** Pausing the game itself would need
  input injection. The game keeps running while the helper is paused.
- **Powerups are not live-validated yet.** The round-state logic is tested
  offline with a covered card, a moved card, and a re-dealt card. Free Daub,
  Pick Me Up, Instant Ball, and boosters still need a real-game pass; if one
  covers the card in an unusual way, send a screenshot.
- **75-ball only** (B/I/N/G/O, 1–75). A 90-ball mode would need changes.
- **The ball must be above the card**, as in your screenshots. If the layout
  changes, send a screenshot.
- **A pause banner or popup covering the card** makes the card unreadable. The
  helper hides the overlay, keeps the saved round, and recovers when the banner
  goes away. It will not re-scan the card just because a popup appeared.
- **One cell at a time.** If a powerup calls several balls quickly (Instant
  Ball), the newest interpreted call is the one shown; you may need to daub the
  earlier ones yourself. This is the simple, safe behavior we chose.
- It captures the visible screen with DXGI. Nothing is recorded or saved unless
  you run the test commands.

## Files

| File | Purpose |
|------|---------|
| `app.py` | Live loop: capture, one card scan per round, ball polling, round-state check, overlay, control bar |
| `controls.py` | Always-on-top no-focus control bar (Scan / Pause / Quit) |
| `overlay.py` | Topmost click-through per-pixel-alpha window |
| `vision.py` | Grid, card, ball, OCR, validation |
| `run_one.py` | Offline single-screenshot tester |
| `start_silent.vbs` | No-console launcher (daily use) |
| `start.bat` | Console launcher (debugging) |
| `install_shortcut.ps1` | Creates the `Ctrl+Alt+B` Desktop shortcut |
| `bingo.log` | Runtime log (created automatically) |
| `bar_pos.txt` | Saved control-bar position (created after dragging) |
| `image.png`, `Screenshot ...png` | Reference cards |
| `test_oncard.png` | On-card test fixture (`B 13`) |
