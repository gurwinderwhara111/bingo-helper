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

- **Scanning is continuous.** It does not wait for a keypress per call.
- **Card moved / resized / zoomed?** A watchdog re-detects the grid every 2
  seconds and re-anchors the overlay. No `F8` needed.
- **New round?** The card change is detected and re-read automatically.
- **Ball changes?** The region above the card is diffed ~8 times per second;
  OCR only runs when something changed.
- **Paused accidentally?** It keeps retrying every 2 seconds until the card is
  readable again.
- **Only one copy runs.** Launching it twice does nothing; use the bar to quit.

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

- **Number on the card** → grid goes dark, one cell stays clear.
  Bar: `B 13 -> row 1 col 1`
- **Number not on the card** → no overlay (nothing is hidden).
  Bar: `B 5 -> not on card`
- **New round** → card change is detected automatically and re-read.

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
- **Real-game validation is still pending.** The vision path is tested on two
  screenshots at two scales; auto re-anchoring is tested by shifting a
  screenshot. A different skin may need a new screenshot from you.
- **75-ball only** (B/I/N/G/O, 1–75). A 90-ball mode would need changes.
- **The ball must be above the card**, as in your screenshots. If the layout
  changes, send a screenshot.
- **A pause banner covering the card** makes the card unreadable. The helper
  hides the overlay and retries; it recovers when the banner goes away.
- It captures the visible screen with DXGI. Nothing is recorded or saved unless
  you run the test commands.

## Files

| File | Purpose |
|------|---------|
| `app.py` | Live loop: capture, detect, poll ball, overlay, watchdog, control bar |
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
