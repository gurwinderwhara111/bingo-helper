"""Live bingo helper: DXGI capture -> card/ball vision -> dim overlay with a hole.

Run it while the game is visible on screen:

    python app.py          (console, for debugging)
    start_silent.vbs       (no console, daily use)

The helper scans continuously and re-detects the card on its own. An always-on-top
control bar provides Scan / Pause / Resume / Quit without stealing focus.

Hotkeys are optional fallbacks:
    F8   re-detect the card
    F9   toggle the overlay
    F10  quit
"""
from __future__ import annotations

import ctypes
import logging
import os
import sys
import time
from ctypes import wintypes

import cv2
import numpy as np

import bingo as B
import vision as V
from controls import ControlBar
from overlay import Overlay

VERSION = "1.13"

logging.disable(logging.INFO)

VK_F8, VK_F9, VK_F10 = 0x77, 0x78, 0x79
WM_HOTKEY = 0x0312
PM_REMOVE = 0x0001

CELL_CMP = 48          # size used to compare one cell with its round baseline
CELL_DIFF = 12.0       # mean per-pixel diff that counts as "this cell changed"
DAUB_DIFF = 18.0       # stronger diff that confirms a real daub
DAUB_SAT = 0.40        # daub fill must also cover this much of the cell
ROUND_CHANGE = 12      # changed cells that mean "card covered or re-dealt"
ROUND_CHECK = 0.3      # seconds between cheap cell-change checks
HIGHLIGHT_TIMEOUT = 0.0 # 0 disables stale auto-hide: never remove valid guidance
GRID_STABLE_DIFF = 8.0 # saved grid-crop diff below this means "grid has not moved"
POPUP_RETRY = 0.5      # seconds between failed Pick Me Up title reads
PICK_RETRY = 0.25      # seconds between Pick Me Up reads while balls pop in
ROUND_READABLE = 23    # cells a switch candidate must read before it is trusted
MIN_CARD_WIDTH = 300   # px - below this, small cells make OCR errors multiply
BALL_SCORE = 0.92      # minimum OCR confidence for a normal ball
INSTANT_SCORE = 0.92   # minimum OCR confidence for an Instant Ball number

user32 = ctypes.WinDLL("user32", use_last_error=True)
kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)


class MSG(ctypes.Structure):
    _fields_ = [
        ("hwnd", wintypes.HWND),
        ("message", wintypes.UINT),
        ("wParam", wintypes.WPARAM),
        ("lParam", wintypes.LPARAM),
        ("time", wintypes.DWORD),
        ("pt", wintypes.POINT),
    ]


user32.PeekMessageW.argtypes = [ctypes.POINTER(MSG), wintypes.HWND,
                                wintypes.UINT, wintypes.UINT, wintypes.UINT]
user32.PeekMessageW.restype = wintypes.BOOL
user32.RegisterHotKey.argtypes = [wintypes.HWND, ctypes.c_int,
                                  wintypes.UINT, wintypes.UINT]
user32.RegisterHotKey.restype = wintypes.BOOL
user32.UnregisterHotKey.argtypes = [wintypes.HWND, ctypes.c_int]
user32.UnregisterHotKey.restype = wintypes.BOOL
user32.TranslateMessage.argtypes = [ctypes.POINTER(MSG)]
user32.TranslateMessage.restype = wintypes.BOOL
user32.DispatchMessageW.argtypes = [ctypes.POINTER(MSG)]
user32.DispatchMessageW.restype = ctypes.c_ssize_t

BASE_DIR = os.path.dirname(os.path.abspath(__file__))


def setup_logging() -> None:
    """Tee stdout to bingo.log so the silent launcher leaves a trace."""
    path = os.path.join(BASE_DIR, "bingo.log")
    try:
        if os.path.exists(path) and os.path.getsize(path) > 1_000_000:
            os.replace(path, path + ".old")
    except Exception:
        pass
    try:
        log = open(path, "a", encoding="utf-8", buffering=1)
    except Exception:
        return

    class Tee:
        def __init__(self, *streams):
            self.streams = streams

        def write(self, text):
            for stream in self.streams:
                try:
                    stream.write(text)
                    stream.flush()
                except Exception:
                    pass

        def flush(self):
            for stream in self.streams:
                try:
                    stream.flush()
                except Exception:
                    pass

    if sys.stdout is None:
        sys.stdout = log
    else:
        sys.stdout = Tee(sys.stdout, log)
    sys.stderr = sys.stdout


def single_instance():
    """Return a mutex handle, or None if another copy is already running."""
    kernel32.CreateMutexW.argtypes = [wintypes.LPVOID, wintypes.BOOL,
                                      wintypes.LPCWSTR]
    kernel32.CreateMutexW.restype = wintypes.HANDLE
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel32.CloseHandle.restype = wintypes.BOOL
    handle = kernel32.CreateMutexW(None, False, "BingoHelper_GS_v1")
    if not handle:
        return None
    if ctypes.get_last_error() == 183:  # ERROR_ALREADY_EXISTS
        kernel32.CloseHandle(handle)
        return None
    return handle


def set_dpi_aware() -> None:
    try:
        user32.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4))
    except Exception:
        try:
            ctypes.windll.shcore.SetProcessDpiAwareness(2)
        except Exception:
            pass


def register_hotkeys() -> None:
    for kid, vk, name in ((1, VK_F8, "F8"), (2, VK_F9, "F9"), (3, VK_F10, "F10")):
        if not user32.RegisterHotKey(None, kid, 0, vk):
            print(f"warning: could not register {name} (already in use?)")


def pump_messages() -> list[int]:
    """Pump this thread's windows (bar paint/clicks) and return hotkey ids."""
    msg = MSG()
    hits = []
    while user32.PeekMessageW(ctypes.byref(msg), None, 0, 0, PM_REMOVE):
        if msg.message == WM_HOTKEY:
            hits.append(int(msg.wParam))
        else:
            user32.TranslateMessage(ctypes.byref(msg))
            user32.DispatchMessageW(ctypes.byref(msg))
    return hits


class App:
    def __init__(self) -> None:
        self.cam = None
        self.engine = None
        self.overlay = None
        self.bar: ControlBar | None = None
        self.mutex = None
        self.grid: V.Grid | None = None
        self.card = None
        self.card_scores = None
        self.free_cells: set[tuple[int, int]] = {(2, 2)}
        self.daubed = B.initial_daubed()
        self.line_counts = B.line_counts(self.daubed)
        self.last_calls: set[tuple[str, int]] = set()
        self.not_on_card: dict[tuple[str, int], float] = {}
        self.last_unread_log = 0.0
        self.current_cells: list[tuple[int, int]] = []
        self.instant_cells: set[tuple[int, int]] = set()
        self.instant_offered: set[tuple[str, int]] = set()
        self.pending_cells: dict[tuple[int, int], float] = {}
        self.pending: tuple[str, int] | None = None
        self.pending_n = 0
        self.pending_t0 = 0.0
        self.last_ball_crop = None
        self.overlay_enabled = True
        self.running = True
        self.paused = False
        self.rescan_requested = False
        self.max_frames = 0
        self.max_polls = 0
        self.source_image = None      # offline test: use this instead of capture
        self.dry_run = False          # offline test: never show the overlay
        self.last_grid_crop = None
        self.cell_baseline = None     # 25 cell crops from the start of the round
        self.card_visible = False     # saved card is on screen and trusted
        self.pending_card = None      # different card waiting for a 2nd read
        self.pending_card_n = 0
        self.last_round_check = 0.0
        self.last_round_ocr = 0.0
        self.last_detect_try = 0.0
        self.detect_fail = 0
        self.popup = None             # cached Pick Me Up result
        self.last_popup_check = 0.0
        self.popup_wait_until = 0.0   # holding state while offers pop in
        self.lock_candidate = None    # two agreeing reads lock a fresh scan
        self.lock_candidate_n = 0
        self.last_verify = 0.0        # last miss-driven card re-read
        self.last_popup_text = ""
        self.last_instant_check = 0.0
        self.instant_banner = None
        self.instant_active = False
        self.highlight_deadline = 0.0
        self.initial_uncertain = False

    # ------------------------------------------------------------ status

    def status(self, line1: str, line2: str = "", color: str = "green") -> None:
        if self.bar is not None:
            self.bar.set_status(line1, line2, color)

    def _toggle_pause(self) -> None:
        self.paused = not self.paused
        if self.bar is not None:
            self.bar.set_paused(self.paused)
        if self.paused:
            self.overlay.hide()
            self.status("PAUSED", "click resume", "amber")
            print("paused")
        else:
            self.last_ball_crop = None
            self.pending = None
            frame = self.grab()
            restore = (self.grid is not None and bool(self.current_cells)
                       and self.overlay_enabled)

            if frame is not None and self.grid is not None:
                # The card may have changed while we were paused. Do not throw
                # the saved round away; hide the hole and let check_round
                # confirm the card. A false highlight is worse than a delay.
                crop = frame[self.grid.y0:self.grid.y1,
                             self.grid.x0:self.grid.x1]
                diff = 999.0
                if (self.last_grid_crop is not None
                        and self.last_grid_crop.shape == crop.shape):
                    diff = float(cv2.absdiff(crop, self.last_grid_crop).mean())
                if diff > 10.0:
                    self.current_cells = []
                    self._set_card_visible(False)
                    self.last_round_check = 0.0
                    self.last_round_ocr = 0.0
                    self.status("SCANNING", "card changed - checking", "amber")
                    print("resumed (card changed)")
                    return

            # Restore the last confirmed cells. If a new ball was called while
            # paused, the next poll replaces this within ~0.25 s.
            self._set_card_visible(True)
            if restore:
                self.show_cells(*self.current_cells)
            self.status("SCANNING", "watching for calls", "green")
            print("resumed")

    def _request_rescan(self) -> None:
        self.paused = False
        if self.bar is not None:
            self.bar.set_paused(False)
        self.rescan_requested = True
        self.status("SCANNING", "re-detecting card...", "green")
        print("rescan requested")

    def _request_quit(self) -> None:
        print("quit requested")
        self.running = False

    # ------------------------------------------------------------ setup

    def start_capture(self) -> None:
        import bettercam
        self.cam = bettercam.create(output_color="BGR")
        self.cam.start(target_fps=20, video_mode=True)
        time.sleep(0.6)
        f = self.cam.get_latest_frame()
        if f is not None:
            print(f"capture: {f.shape[1]}x{f.shape[0]}")

    def grab(self):
        if self.source_image is not None:
            return self.source_image
        if self.cam is None:
            return None
        return self.cam.get_latest_frame()

    # ------------------------------------------------------------ detection

    def _invalidate(self) -> None:
        self.grid = None
        self.card = None
        self.card_scores = None
        self.free_cells = {(2, 2)}
        self.daubed = B.initial_daubed()
        self.line_counts = B.line_counts(self.daubed)
        self.last_calls.clear()
        self.not_on_card.clear()
        self.instant_offered.clear()
        self.current_cells = []
        self.instant_cells.clear()
        self.pending_cells.clear()
        self.pending = None
        self.pending_n = 0
        self.last_ball_crop = None
        self.last_grid_crop = None
        self.cell_baseline = None
        self.card_visible = False
        self.pending_card = None
        self.pending_card_n = 0
        self.last_round_check = 0.0
        self.last_round_ocr = 0.0
        self.popup = None
        self.lock_candidate = None
        self.lock_candidate_n = 0
        self.initial_uncertain = False
        self.last_instant_check = 0.0
        self.instant_banner = None
        self.instant_active = False
        self.highlight_deadline = 0.0
        self.overlay.hide()

    def _set_card_visible(self, visible: bool) -> None:
        """Track whether the saved card is on screen and trusted."""
        if visible == self.card_visible:
            return
        self.card_visible = visible
        if visible:
            print("card visible - resuming")
            self.status("SCANNING", "watching for calls", "green")
            if self.current_cells and self.overlay_enabled:
                self.show_cells(*self.current_cells)
        else:
            print("card hidden - waiting")
            self.overlay.hide()
            self.status("CARD HIDDEN", "waiting for card", "amber")

    def _capture_baseline(self, frame) -> None:
        """Save the 25 cell crops from the start of the round."""
        if self.grid is None:
            self.cell_baseline = None
            return
        crops = []
        g = self.grid
        for r in range(5):
            for c in range(5):
                cell = frame[g.ys[r]:g.ys[r + 1], g.xs[c]:g.xs[c + 1]]
                if cell.size == 0:
                    self.cell_baseline = None
                    return
                crops.append(cv2.resize(cell, (CELL_CMP, CELL_CMP),
                                        interpolation=cv2.INTER_AREA))
        self.cell_baseline = np.stack(crops).astype(np.int16)

    def _cell_diffs(self, frame, grid=None) -> list[float]:
        """Per-cell difference from the saved round baseline (25 values)."""
        g = grid if grid is not None else self.grid
        if g is None or self.cell_baseline is None:
            return [999.0] * 25
        crops = []
        for r in range(5):
            for c in range(5):
                cell = frame[g.ys[r]:g.ys[r + 1], g.xs[c]:g.xs[c + 1]]
                if cell.size == 0:
                    return [999.0] * 25
                crops.append(cv2.resize(cell, (CELL_CMP, CELL_CMP),
                                        interpolation=cv2.INTER_AREA))
        cur = np.stack(crops).astype(np.int16)
        diff = np.abs(cur - self.cell_baseline).mean(axis=(1, 2, 3))
        return [float(v) for v in diff]

    def _changed_cells(self, frame, grid=None) -> int:
        """How many of the 25 cells differ from the saved round baseline."""
        return sum(1 for d in self._cell_diffs(frame, grid) if d > CELL_DIFF)

    def _reset_daubed(self, free=None) -> None:
        """Start a fresh round's authoritative daub matrix."""
        if free is not None:
            self.free_cells = {(r, c) for r in range(5) for c in range(5)
                               if free[r][c]}
        else:
            self.free_cells = {(2, 2)}
        self.daubed = B.initial_daubed(self.free_cells)
        self.line_counts = B.line_counts(self.daubed)

    def _seed_daubs(self, warped, free) -> set[tuple[int, int]]:
        """Reset the round and read daubs already on screen.

        This is what lets the helper start in the middle of a round without
        pretending the board is fresh.  Ambiguous colour makes Pick Me Up
        choose manually for that round.
        """
        self._reset_daubed(free)
        daubed, ambiguous = V.detect_daubed_cells(warped, free)
        for r, c in daubed:
            self.daubed[r][c] = True
        self.line_counts = B.line_counts(self.daubed)
        self.initial_uncertain = ambiguous
        return daubed

    def _mark_daubed(self, r: int, c: int) -> bool:
        if self.daubed[r][c]:
            return False
        self.daubed[r][c] = True
        self.line_counts = B.line_counts(self.daubed)
        return True

    def _apply_daubs(self, diffs, frame=None) -> list[tuple[int, int]]:
        """Advance the daub matrix for cells that really changed.

        Card pixels are the source of truth. A highlight is only provisional;
        this is what makes the bingo/Pick Me Up answer correct.
        """
        new = []
        before_cells = list(self.current_cells)
        for i, d in enumerate(diffs):
            if d < DAUB_DIFF:
                continue
            r, c = divmod(i, 5)
            # A popup or hover can change a cell without daubing it. Real
            # daubs in this game fill the cell with saturated colour.
            if frame is not None and self._cell_saturation(frame, r, c) < DAUB_SAT:
                continue
            if self._mark_daubed(r, c):
                new.append((r, c))
            self.pending_cells.pop((r, c), None)
            self.instant_cells.discard((r, c))
            if (r, c) in self.current_cells:
                self.current_cells.remove((r, c))
        if new:
            names = B.completed_lines(self.daubed)
            print("daub confirmed: " + ", ".join(f"r{r + 1}c{c + 1}"
                                                 for r, c in new)
                  + (f"  [{len(names)} BINGO]" if names else ""))
            if self.card_visible and self.overlay_enabled:
                if self.current_cells:
                    self.show_cells(*self.current_cells)
                else:
                    self.overlay.hide()
            # If the real daub happened outside the highlighted cell(s), our
            # provisional hole was wrong.  Never leave a wrong highlight alive.
            if (before_cells
                    and all(pos not in before_cells for pos in new)):
                self.current_cells = []
                self.pending_cells.clear()
                self.instant_cells.clear()
                self.highlight_deadline = 0.0
                self.overlay.hide()
                print("provisional highlight cleared - wrong/unconfirmed cell")
        return new

    def _cell_diff(self, frame, r: int, c: int, grid=None) -> float:
        g = grid if grid is not None else self.grid
        if g is None or self.cell_baseline is None:
            return 999.0
        cell = frame[g.ys[r]:g.ys[r + 1], g.xs[c]:g.xs[c + 1]]
        if cell.size == 0:
            return 999.0
        cur = cv2.resize(cell, (CELL_CMP, CELL_CMP),
                         interpolation=cv2.INTER_AREA).astype(np.int16)
        return float(np.abs(cur - self.cell_baseline[r * 5 + c]).mean())

    def _cell_saturation(self, frame, r: int, c: int, grid=None) -> float:
        g = grid if grid is not None else self.grid
        if g is None:
            return 0.0
        cell = frame[g.ys[r]:g.ys[r + 1], g.xs[c]:g.xs[c + 1]]
        return V.cell_saturation(cell)

    def _cell_saturation_from_last_crop(self, r: int, c: int) -> float:
        g, crop = self.grid, self.last_grid_crop
        if g is None or crop is None:
            return 0.0
        if crop.shape[:2] != (g.y1 - g.y0, g.x1 - g.x0):
            return 0.0
        cell = crop[g.ys[r] - g.y0:g.ys[r + 1] - g.y0,
                    g.xs[c] - g.x0:g.xs[c + 1] - g.x0]
        return V.cell_saturation(cell)

    def _cell_diff_from_last_crop(self, r: int, c: int) -> float:
        """Check a cell in the last frame that still showed the card."""
        g = self.grid
        crop = self.last_grid_crop
        if g is None or crop is None or self.cell_baseline is None:
            return 999.0
        if crop.shape[:2] != (g.y1 - g.y0, g.x1 - g.x0):
            return 999.0
        ly0, ly1 = g.ys[r] - g.y0, g.ys[r + 1] - g.y0
        lx0, lx1 = g.xs[c] - g.x0, g.xs[c + 1] - g.x0
        cell = crop[ly0:ly1, lx0:lx1]
        if cell.size == 0:
            return 999.0
        cur = cv2.resize(cell, (CELL_CMP, CELL_CMP),
                         interpolation=cv2.INTER_AREA).astype(np.int16)
        return float(np.abs(cur - self.cell_baseline[r * 5 + c]).mean())

    def _confirm_pending(self, frame) -> None:
        """Fast visual confirmation of the cell(s) we just highlighted."""
        if (not self.pending_cells or not self.card_visible
                or self.popup is not None or self.grid is None):
            return
        # If the card is covered or re-dealt, a popup pixel would look like a
        # daub.  Only confirm a pending cell while the rest of the card still
        # matches its round baseline.
        diffs = self._cell_diffs(frame)
        if sum(1 for d in diffs if d > CELL_DIFF) >= ROUND_CHANGE:
            return
        for pos in list(self.pending_cells):
            r, c = pos
            diff = self._cell_diff(frame, r, c)
            if (diff >= DAUB_DIFF
                    and self._cell_saturation(frame, r, c) >= DAUB_SAT):
                self._mark_daubed(r, c)
                self.pending_cells.pop(pos, None)
                self.instant_cells.discard(pos)
                if pos in self.current_cells:
                    self.current_cells.remove(pos)
                print(f"daub confirmed: r{r + 1}c{c + 1}")
                if self.overlay_enabled:
                    if self.current_cells:
                        self.show_cells(*self.current_cells)
                    else:
                        self.overlay.hide()

    def _confirm_pending_from_last_crop(self) -> None:
        """Use the last card frame before a popup covered it."""
        for pos in list(self.pending_cells):
            r, c = pos
            if (self._cell_diff_from_last_crop(r, c) >= DAUB_DIFF
                    and self._cell_saturation_from_last_crop(r, c) >= DAUB_SAT):
                self._mark_daubed(r, c)
                self.pending_cells.pop(pos, None)
                self.instant_cells.discard(pos)

    def _bingo_suffix(self) -> str:
        names = B.completed_lines(self.daubed)
        return f" | {len(names)} BINGO" if names else ""

    @staticmethod
    def _grid_moved(a: V.Grid, b: V.Grid) -> bool:
        """Ignore 1-2 px jitter; treat a real move as a re-anchor."""
        return (abs(a.x0 - b.x0) > 2 or abs(a.y0 - b.y0) > 2
                or abs(a.x1 - b.x1) > 2 or abs(a.y1 - b.y1) > 2)

    def detect(self, quiet: bool = False) -> bool:
        """Find the card and lock a round. This is the only full scan."""
        self.last_detect_try = time.time()
        if self.overlay is not None:
            self.overlay.hide()  # never let a dim rect pollute the card read
        frame = self.grab()
        had_card = self.card is not None

        if frame is None:
            if not quiet:
                print("no frame from capture")
            self.detect_fail += 1
            if had_card:
                self._set_card_visible(False)
                if not quiet:
                    self.status("CARD HIDDEN", "no capture - keeping card", "amber")
            else:
                self._invalidate()
                if not quiet:
                    self.status("WAITING FOR CARD", "no capture yet", "red")
            return False

        grid = V.find_grid(frame)
        if grid is None:
            if not quiet:
                print("grid not found - waiting for the card...")
            self.detect_fail += 1
            if had_card:
                self._set_card_visible(False)
                if not quiet:
                    self.status("CARD HIDDEN", "keeping saved card", "amber")
            else:
                self._invalidate()
                if not quiet:
                    self.status("WAITING FOR CARD", "keep the game visible", "amber")
            return False

        if grid.width < MIN_CARD_WIDTH:
            print(f"grid only {grid.width}px wide - maximize the window "
                  "for reliable reads")
            self.status("CARD FOUND", "card small - maximize window", "amber")

        warped = V.rectify(frame, grid)
        card, scores, free = V.read_card_auto(self.engine, warped)
        issues = V.validate_card(card, free)
        if issues:
            # A wrong card means wrong highlights. Never accept one.
            if not quiet:
                print(f"card read rejected: {issues[:3]}")
            self.detect_fail += 1
            if had_card:
                self._set_card_visible(False)
                if not quiet:
                    self.status("CARD UNREADABLE", "keeping saved card", "amber")
            else:
                self._invalidate()
                if not quiet:
                    self.status("CARD UNREADABLE", "retrying...", "red")
            return False

        # Two agreeing reads lock a fresh scan. One deal-animation frame
        # must never become the round.
        if card != self.lock_candidate:
            self.lock_candidate = card
            self.lock_candidate_n = 1
        else:
            self.lock_candidate_n += 1
        if self.lock_candidate_n < 2:
            if not quiet:
                print("card candidate - confirming before lock")
            self.status("CARD FOUND", "confirming card...", "amber")
            return False
        self.lock_candidate = None
        self.lock_candidate_n = 0

        # Valid card: lock this round. All later calls use this saved copy.
        self.grid = grid
        self.card = card
        self.card_scores = scores
        initial_daubs = self._seed_daubs(warped, free)
        self.last_calls.clear()
        self.not_on_card.clear()
        self.current_cells = []
        self.instant_cells.clear()
        self.instant_offered.clear()
        self.pending_cells.clear()
        self.pending = None
        self.pending_n = 0
        self.last_ball_crop = None
        self.pending_card = None
        self.pending_card_n = 0
        self.last_round_check = 0.0
        self.last_round_ocr = 0.0
        self.popup = None
        self.last_popup_check = 0.0
        self.popup_wait_until = 0.0
        self._capture_baseline(frame)
        self.last_grid_crop = frame[grid.y0:grid.y1, grid.x0:grid.x1].copy()
        self.overlay.hide()
        self.card_visible = True
        self.detect_fail = 0
        self.status("SCANNING", "watching for calls", "green")

        print(f"\ngrid: ({grid.x0},{grid.y0})-({grid.x1},{grid.y1})  "
              f"{grid.width}x{grid.height}")
        for row in card:
            print("  " + " ".join(f"{v:>3}" if v is not None else "  -" for v in row))
        extra_free = sorted(self.free_cells - {(2, 2)})
        if extra_free:
            print("extra free/wild: " + ", ".join(f"r{r + 1}c{c + 1}"
                                                  for r, c in extra_free))
        if initial_daubs:
            print("already daubed: " + ", ".join(f"r{r + 1}c{c + 1}"
                                                  for r, c in sorted(initial_daubs)))
        if self.initial_uncertain:
            print("daub state ambiguous - Pick Me Up will ask manually")
        print("constraints: OK")
        return True

    # ------------------------------------------------------------ ball

    def _ball_region(self, frame):
        h, w = frame.shape[:2]
        x0 = max(0, self.grid.x0 - 30)
        x1 = min(w, self.grid.x1 + 30)
        y0 = max(0, self.grid.y0 - int(self.grid.width * 0.85))
        y1 = max(1, self.grid.y0)
        return x0, y0, x1, y1

    def _sub_grid(self, dx: int, dy: int) -> V.Grid:
        g = self.grid
        return V.Grid(x0=g.x0 - dx, y0=g.y0 - dy, x1=g.x1 - dx, y1=g.y1 - dy,
                      xs=[x - dx for x in g.xs], ys=[y - dy for y in g.ys])

    def _handle_ball(self, frame, bbox) -> None:
        """Read and confirm one normal called ball.

        This path is intentionally biased toward coverage: if the ball can be
        read reliably once, do not wait for a second poll and risk missing the
        next call.  Low-confidence reads are still retried instead of guessed.
        """
        ball = V.read_ball_robust(self.engine, frame, bbox,
                                  min_score=BALL_SCORE)
        num = ball["number"]
        if num is None or not (1 <= num <= 75) or ball["number_score"] < BALL_SCORE:
            self.last_ball_crop = None  # retry the read, keep any pending call
            # A miss must never be silent: log it (throttled) so a ball that
            # was on screen and never read leaves a trace in bingo.log.
            nowt = time.time()
            if nowt - self.last_unread_log >= 1.0:
                self.last_unread_log = nowt
                print(f"ball unread (score {ball['number_score']:.2f})"
                      " - retrying")
            return

        derived = V.letter_for(num)
        if derived is None:
            self.last_ball_crop = None
            return
        letter = derived
        key = (letter, num)
        if key in self.last_calls:
            self.pending = None
            self.pending_n = 0
            return

        self.pending = key
        self.pending_n = 1
        self.pending_t0 = time.perf_counter()
        self.last_calls.add(key)
        self.pending_n = 0
        self.commit(letter, num, frame)

    def _instant_ball_active(self, frame, bboxes=None) -> bool:
        """True only while a real Instant Ball is on screen.

        Two independent confirmations prevent the small called-ball history row
        from becoming fake Instant Ball guidance: the game's own banner when it
        is present, or three large balls aligned in one row as in the official
        power-up screenshot.
        """
        now = time.time()
        if now - self.last_instant_check < 0.25:
            return self.instant_active
        self.last_instant_check = now
        self.instant_active = False
        if bboxes and V.detect_instant_ball(frame, self.grid, bboxes):
            self.instant_active = True
        else:
            self.instant_banner = V.find_instant_ball_banner(frame, self.grid)
            self.instant_active = self.instant_banner is not None
        return self.instant_active

    def poll_ball(self, frame) -> None:
        if self.card is None:
            return
        # Ball reads keep running while a Pick Me Up-style popup is open:
        # the WorldWinner clock keeps running during these power-ups, so a
        # normal call that arrives in that window is a real ball that must
        # not be dropped.  Commits are safe then: locating a number uses the
        # saved card, and missing highlights revive when the card returns.
        # (Card hidden WITHOUT a popup - fading card, unconfirmed switch -
        # still suppresses reads, exactly as before.)
        if not self.card_visible and self.popup is None:
            return
        x0, y0, x1, y1 = self._ball_region(frame)
        crop = frame[y0:y1, x0:x1]
        if crop.size == 0:
            return

        if self.last_ball_crop is not None and self.last_ball_crop.shape == crop.shape:
            diff = float(cv2.absdiff(crop, self.last_ball_crop).mean())
        else:
            diff = 999.0
        if diff < 3.0 and self.pending_n == 0:
            return
        self.last_ball_crop = crop.copy()

        # A pink modal whose title physically starts above the card covers
        # the ball strip itself - its own balls would pollute every read, so
        # skip this frame.  A title lower on the card (Pick Me Up over the
        # card body) leaves the strip untouched and reads continue.
        band = V.find_pink_band(frame)
        if (band is not None and band[1] < self.grid.y0
                and self._changed_cells(frame) >= ROUND_CHANGE):
            return

        bboxes = V.find_balls(crop, self._sub_grid(x0, y0), max_balls=3)
        if not bboxes:
            self.pending = None
            self.pending_n = 0
            self.last_ball_crop = None  # keep looking every poll
            return
        bboxes = [(b[0] + x0, b[1] + y0, b[2], b[3]) for b in bboxes]

        # Instant Ball shows two or three large balls at once.  Do not make
        # the user wait for a second confirming read; these balls disappear.
        # Before treating a multi-ball frame as Instant Ball, rule out Pick Me
        # Up: its four offered balls can otherwise look like free balls and
        # produce a wrong highlight.  History balls above the card can also
        # produce several boxes; only the game's INSTANT BALL banner makes
        # those balls count, otherwise treat it as one current ball.
        if len(bboxes) >= 2:
            # Only scan for a new popup title when none is known; an active
            # popup is maintained in check_round, and re-blocking ball reads
            # here is what used to freeze normal detection for whole popups.
            if self.popup is None:
                self.last_popup_check = 0.0
                if self._check_pick_me_up(frame):
                    return
            if self._instant_ball_active(frame, bboxes):
                self._poll_instant(frame, bboxes)
                return
            # Current ball, geometry-validated (v1.13): hot-zone + size band
            # + rightmost.  "Largest wins" read the seated cat blob instead of
            # the real ball in 18/28 files on the labelled corpus.

            bbox = V.select_current(bboxes, self.grid)
            self._handle_ball(frame, bbox)
            return

        bbox = bboxes[0]
        self._handle_ball(frame, bbox)

    def _poll_instant(self, frame, bboxes) -> None:
        """Read all large balls and show all matching holes together."""
        found = []
        unread = 0
        max_unread = 0.0
        for bbox in bboxes:
            ball = V.read_ball_robust(self.engine, frame, bbox,
                                      min_score=INSTANT_SCORE)
            num = ball["number"]
            if (num is None or not (1 <= num <= 75)
                    or ball["number_score"] < INSTANT_SCORE):
                unread += 1
                max_unread = max(max_unread, ball["number_score"])
                continue
            letter = V.letter_for(num) or "?"
            key = (letter, num)
            if key in self.last_calls:
                continue
            if any(k == key for k, _, _ in found):
                continue
            pos = V.locate_number(self.card, num)
            if pos is None:
                # Powerup offer balls: announce each distinct number once
                # and move on.  Never guide a daub for a ball that is not
                # on the card (wrong daubs cost points).
                if key not in self.instant_offered:
                    self.instant_offered.add(key)
                    print(f"powerup offer {letter} {num} - not on card")
                continue
            r, c = pos
            if self.daubed[r][c]:
                continue
            found.append((letter, num, pos))

        if not found:
            if unread:
                nowt = time.time()
                if nowt - self.last_unread_log >= 1.0:
                    self.last_unread_log = nowt
                    print(f"instant ball unread (best score "
                          f"{max_unread:.2f}) - retrying")
                self.last_ball_crop = None
            return

        now = time.time()
        for letter, num, pos in found:
            self.last_calls.add((letter, num))
            self.pending_cells[pos] = now
        self.pending = None
        self.pending_n = 0
        # Keep holes from an earlier partial Instant read while this powerup
        # is still on screen; a later frame must not hide a ball that was
        # already shown just because OCR caught the remaining one now.
        merged = {pos for pos in self.instant_cells
                  if not self.daubed[pos[0]][pos[1]]}
        merged |= {pos for _, _, pos in found}
        self.instant_cells = merged
        self.current_cells = sorted(merged)
        self.highlight_deadline = time.time() + HIGHLIGHT_TIMEOUT
        summary = " ".join(f"{letter}{num}" for letter, num, _ in found)
        print(f"INSTANT BALL: {summary}")
        suffix = self._bingo_suffix()
        self.status("INSTANT BALL", summary + suffix, "green")
        self.show_cells(*self.current_cells)
        if unread:
            # Allow a later frame to pick up any ball that failed OCR.
            self.last_ball_crop = None

    @staticmethod
    def _card_readable(card) -> int:
        return sum(1 for row in card for v in row if v is not None)

    def _verify_card(self, frame) -> None:
        """One fresh-eyes re-read after a missed call.

        A bad initial lock misses one call, then never heals: the wrong
        value sits there for the rest of the round. This re-reads the card
        without replaying locked daub values and patches at most three
        disagreeing cells immediately.
        """
        if self.card is None or self.grid is None or frame is None:
            return
        now = time.time()
        if now - self.last_verify < 2.0:
            return
        self.last_verify = now
        warped = V.rectify(frame, self.grid)
        if warped is None:
            return
        daubed_cells = {(r, c) for r in range(5) for c in range(5)
                        if self.daubed[r][c]}
        plain, plain_scores, plain_free = V.read_card_auto(
            self.engine, warped, daubed=daubed_cells, old=None)
        if V.validate_card(plain, plain_free):
            return
        if self._card_readable(plain) < ROUND_READABLE:
            return
        diff = [(r, c) for r in range(5) for c in range(5)
                if plain[r][c] != self.card[r][c]]
        if 1 <= len(diff) <= 3:
            fixes = ", ".join(
                f"r{r + 1}c{c + 1} "
                f"{self.card[r][c] if self.card[r][c] else '?'}->{plain[r][c]}"
                for r, c in diff)
            for r, c in diff:
                self.card[r][c] = plain[r][c]
            self.card_scores = plain_scores
            print("card corrected (miss re-read): " + fixes)
            self.status("SCANNING", "card corrected", "green")
            self._retry_missed()

    def _retry_missed(self) -> None:
        """Re-commit balls scored NOT ON CARD before the card healed.

        commit() keys a ball into last_calls before it knows whether the
        card covers it; a self-healed card otherwise can never highlight
        that ball again.  This is the release valve.
        """
        if not self.not_on_card:
            return
        for key in [k for k, t in self.not_on_card.items()
                    if time.time() - t < 30.0]:
            letter, num = key
            if self.card is None or V.locate_number(self.card, num) is None:
                continue
            self.not_on_card.pop(key)
            self.last_calls.discard(key)
            self.commit(letter, num, frame=None)

    def commit(self, letter: str, num: int, frame=None) -> None:
        ms = (time.perf_counter() - self.pending_t0) * 1000 if self.pending_t0 else 0.0
        self.pending_t0 = 0.0
        self.instant_cells.clear()
        self.instant_offered.clear()
        self.highlight_deadline = 0.0
        pos = V.locate_number(self.card, num)
        # pre-add keeps an already-seen ball from re-committing every poll
        self.last_calls.add((letter, num))
        if pos is None:
            # Wrong daubs cost -100 points in the live game, and most
            # NOT-ON-CARD calls are genuinely not on the card: no guessing
            # a daub on an unreadable cell.  Remember the miss instead, so
            # a later self-healed card can re-commit this ball and finally
            # light it up.
            self.not_on_card[(letter, num)] = time.time()
            # Missed a call the card should have covered: re-read the card
            # once with fresh eyes so a bad lock heals on the first miss.
            self._verify_card(frame)
            print(f"{letter} {num} -> NOT ON CARD  ({ms:.0f} ms)")
            self.current_cells = []
            if self.overlay is not None:
                self.overlay.hide()
            self.status("SCANNING" if self.card_visible else "CARD HIDDEN",
                        f"{letter} {num} -> not on card",
                        "green" if self.card_visible else "amber")
            return
        self.not_on_card.pop((letter, num), None)

        r, c = pos
        if self.daubed[r][c]:
            print(f"{letter} {num} -> r{r + 1}c{c + 1} already daubed")
            self.current_cells = []
            if self.overlay is not None:
                self.overlay.hide()
            self.status("SCANNING" if self.card_visible else "CARD HIDDEN",
                        f"{letter} {num} -> already daubed",
                        "green" if self.card_visible else "amber")
            return

        self.current_cells = [pos]
        self.pending_cells[pos] = time.time()
        self.highlight_deadline = time.time() + HIGHLIGHT_TIMEOUT
        print(f"{letter} {num} -> row {r + 1} col {c + 1}  ({ms:.0f} ms)")
        suffix = self._bingo_suffix()
        self.status("SCANNING" if self.card_visible else "CARD HIDDEN",
                    f"{letter} {num} -> r{r + 1}c{c + 1}{suffix}",
                    "green" if self.card_visible else "amber")
        if self.card_visible and self.overlay_enabled:
            self.show_cells(*self.current_cells)

    # ------------------------------------------------------------ overlay

    def _cell_rect(self, r: int, c: int):
        g = self.grid
        x0, y0 = g.xs[c], g.ys[r]
        return (x0, y0, g.xs[c + 1] - x0, g.ys[r + 1] - y0)

    def show_cells(self, *cells) -> None:
        if not self.card_visible:
            return
        clean = []
        for r, c in cells:
            if (r, c) not in clean and not self.daubed[r][c]:
                clean.append((r, c))
        self.current_cells = clean
        if not clean:
            self.overlay.hide()
            return
        if self.dry_run:
            print("overlay holes -> grid=" + str(self.grid.rect) + " cells="
                  + str([self._cell_rect(r, c) for r, c in clean]))
            return
        holes = [self._cell_rect(r, c) for r, c in clean]
        self.overlay.show(self.grid.rect, holes=holes, dim_alpha=235)
        if self.bar is not None:
            self.bar.raise_()

    def show_cell(self, r: int, c: int) -> None:
        self.show_cells((r, c))

    # ------------------------------------------------------------ round state

    def _check_pick_me_up(self, frame) -> bool:
        """Detect and rank Pick Me Up without ever guessing on a bad read."""
        if self.popup is not None:
            # The popup stays static; do not OCR four balls every 0.3 s.
            # Check only that its pink title strip is still there.  Without
            # this, a closed popup plus many daubs would look like a large
            # change forever and the card would never come back.
            title = self.popup.get("title")
            band = V.find_pink_band(frame)
            if title is None or band is None or self._rect_iou(band, title) < 0.20:
                print("popup gone")
                self.popup = None
                self.instant_offered.clear()
                return False
            return True
        if self.card is None:
            return False
        now = time.time()
        if now < self.popup_wait_until:
            # Offered balls are still animating in; treat the popup as
            # handled until the next retry so its ball reads are not wasted.
            return True
        if now - self.last_popup_check < POPUP_RETRY:
            return False
        self.last_popup_check = now

        found = V.find_popup_title(self.engine, frame)
        if not found:
            return False
        title, txt = found
        print(f"popup: {txt!r}")
        self._set_card_visible(False)
        H, W = frame.shape[:2]
        bx, by, bw, bh = title
        pad = max(12, int(0.12 * bw))
        region = (bx - pad, by + bh - 10, bx + bw + pad,
                  min(H, by + bh + int(1.9 * bw)))
        bboxes = V.find_popup_balls(frame, region, max_balls=4)

        offers, details = [], []
        for bbox in bboxes:
            ball = V.read_ball(self.engine, frame, bbox)
            num = ball["number"]
            if (num is None or not (1 <= num <= 75)
                    or ball["number_score"] < INSTANT_SCORE):
                ball = V.read_ball_robust(self.engine, frame, bbox,
                                          min_score=INSTANT_SCORE)
                num = ball["number"]
            if (num is None or not (1 <= num <= 75)
                    or ball["number_score"] < INSTANT_SCORE):
                continue
            letter = ball["letter"]
            derived = V.letter_for(num)
            if letter and derived != letter:
                print(f"PICK ME UP reject: {letter} {num}, range says {derived}")
                continue
            if not letter:
                letter = derived or "?"
            if num in offers:
                continue
            offers.append(num)
            details.append((letter, num, round(ball["number_score"], 3)))
        print(f"PICK ME UP offers: {offers}  {details}")

        # Use the last visible card frame to settle any highlight that was
        # still pending when the popup appeared.
        self._confirm_pending_from_last_crop()
        self.current_cells = []
        self.instant_cells.clear()
        self.overlay.hide()

        if self.initial_uncertain:
            self.popup = {"offers": tuple(offers), "best": None, "manual": True,
                          "reason": "daub state uncertain", "t": now,
                          "title": title}
            self.status("PICK ME UP", "daub state uncertain - manual", "amber")
            print("PICK ME UP: daub state uncertain - manual")
            return True

        if len(offers) < 4:
            # The offered balls pop in one by one.  Do not lock a half-read
            # popup as "manual": re-read it shortly and only fall back when
            # the popup closes before all four balls can be read.
            self.popup = None
            self.popup_wait_until = now + PICK_RETRY
            self.status("PICK ME UP", "reading offers...", "amber")
            print(f"PICK ME UP: {len(offers)} of 4 balls read - retrying")
            return True

        choices = B.rank_picks(self.card, self.daubed, offers)
        for line in B.format_ranking(choices):
            print("  " + line)
        if not choices:
            self.popup = {"offers": tuple(offers), "best": None, "manual": True,
                          "reason": "none on card", "choices": choices, "t": now,
                          "title": title}
            self.status("PICK ME UP", "none on card - choose manually", "amber")
            return True

        uncertain = False
        pending = [pos for pos in self.pending_cells
                   if not self.daubed[pos[0]][pos[1]]]
        if pending:
            alt = [row[:] for row in self.daubed]
            for r, c in pending:
                alt[r][c] = True
            alt_choices = B.rank_picks(self.card, alt, offers)
            top = choices[0].number
            alt_top = alt_choices[0].number if alt_choices else None
            if top != alt_top:
                uncertain = True
                print(f"PICK ME UP: unconfirmed daub changes best "
                      f"({top} vs {alt_top})")

        if uncertain:
            self.popup = {"offers": tuple(offers), "best": None, "manual": True,
                          "reason": "unconfirmed daub",
                          "choices": choices, "t": now, "title": title}
            self.status("PICK ME UP", "last call unconfirmed - manual", "amber")
            return True

        top_score = choices[0].score
        ties = [p for p in choices if p.score == top_score]
        if len(ties) > 1:
            # An exact tie means every tied pick is equally valid. Pick the
            # top one and still show all tied numbers, instead of refusing.
            names = "/".join(str(p.number) for p in ties)
            best = ties[0]
            self.popup = {"offers": tuple(offers), "best": best.number,
                          "manual": False, "reason": "tie",
                          "choices": choices, "t": now, "title": title}
            self.status("PICK ME UP", B.format_tie_choice(ties), "amber")
            print(f"PICK ME UP tie {names}=> PICK {best.number} at "
                  f"r{best.row + 1}c{best.col + 1}")
            return True

        best = choices[0]
        self.popup = {"offers": tuple(offers), "best": best.number,
                      "manual": False, "choices": choices, "t": now,
                      "title": title}
        self.status("PICK ME UP", B.format_choice(best), "green")
        print(f"PICK ME UP -> {best.number} at r{best.row + 1}c{best.col + 1}")
        return True

    @staticmethod
    def _rect_iou(a, b) -> float:
        ax, ay, aw, ah = a
        bx, by, bw, bh = b
        x0, y0 = max(ax, bx), max(ay, by)
        x1, y1 = min(ax + aw, bx + bw), min(ay + ah, by + bh)
        if x1 <= x0 or y1 <= y0:
            return 0.0
        inter = (x1 - x0) * (y1 - y0)
        union = aw * ah + bw * bh - inter
        return inter / union if union > 0 else 0.0

    def check_round(self, frame) -> None:
        """Keep the saved round. Re-read only when the card itself changes."""
        now = time.time()
        if now - self.last_round_check < ROUND_CHECK:
            return
        self.last_round_check = now

        if frame is None or self.grid is None or self.card is None:
            return

        # A provisional highlight must never survive forever.  If the user
        # daubs a different cell, the daub logic clears it immediately; this
        # catches a wrong hole that is simply never confirmed.
        if (HIGHLIGHT_TIMEOUT > 0 and self.current_cells and self.card_visible
                and now > self.highlight_deadline):
            self.current_cells = []
            self.pending_cells.clear()
            self.instant_cells.clear()
            self.highlight_deadline = 0.0
            self.overlay.hide()
            print("provisional highlight cleared - unconfirmed timeout")
            return

        # Locate the grid only when the saved grid-crop changed enough.  This
        # stops the common case from paying full-screen geometry detection on
        # every cheap round check while still re-anchoring on real moves.
        grid = self.grid
        new_grid = None
        stable = False
        if self.grid is not None and self.last_grid_crop is not None:
            crop = frame[self.grid.y0:self.grid.y1,
                         self.grid.x0:self.grid.x1]
            if crop.shape == self.last_grid_crop.shape:
                stable = float(cv2.absdiff(crop, self.last_grid_crop).mean()) < GRID_STABLE_DIFF

        if not stable:
            new_grid = V.find_grid(frame)
            if new_grid is not None:
                grid = new_grid

        diffs = self._cell_diffs(frame, grid)
        changed = sum(1 for d in diffs if d > CELL_DIFF)
        moved = (new_grid is not None and self._grid_moved(self.grid, new_grid))

        if changed < ROUND_CHANGE:
            # Normal daubs/hover, or a popup just cleared. The saved card is
            # still the round. Do not OCR; do not throw anything away.
            if self.popup is not None:
                # The pink band can survive a mostly-visible card; verify
                # before treating popup pixels as daubs.
                if self._check_pick_me_up(frame):
                    return
                print("popup cleared")
                self.popup = None
                self.instant_offered.clear()
            if not moved:
                self._apply_daubs(diffs, frame)
            if moved:
                self.grid = new_grid
                self.last_ball_crop = None
                self.pending = None
                self.pending_n = 0
                self._capture_baseline(frame)
                if (self.card_visible and self.current_cells
                        and self.overlay_enabled):
                    self.show_cells(*self.current_cells)
            self.last_grid_crop = frame[self.grid.y0:self.grid.y1,
                                        self.grid.x0:self.grid.x1].copy()
            if not self.card_visible:
                self._set_card_visible(True)
            return

        # Many cells changed: a popup is covering the card, the card moved,
        # or a new round was dealt. Never dim a popup and never guess.
        was_visible = self.card_visible
        self._set_card_visible(False)

        # Pick Me Up is the only popup we guide.  If this is not it, fall
        # through to the existing card-change check.
        if self._check_pick_me_up(frame):
            return

        cooldown = 0.6 if self.pending_card is not None else 1.0
        if not was_visible and now - self.last_round_ocr < cooldown:
            return
        self.last_round_ocr = now

        # OCR only when the cells really changed.  The fast template path is
        # tried first and falls back to OCR when anything is unreadable.
        warped = V.rectify(frame, grid)
        daubed_cells = {(r, c) for r in range(5) for c in range(5)
                        if self.daubed[r][c]}
        card, scores, free = V.read_card_auto(self.engine, warped,
                                              daubed=daubed_cells,
                                              old=self.card)

        if V.validate_card(card, free):
            # Covered, mid-deal, or unreadable. Keep the saved round and wait.
            self.status("CARD HIDDEN", "waiting for card", "amber")
            return

        if card == self.card:
            # Same card: it moved, was daubed a lot, or the popup cleared.
            # Only trust daub pixels when the grid did not move; a moved grid
            # makes the old baseline noisy, and a false daub is worse than a
            # missed one.
            if not moved:
                self._apply_daubs(diffs, frame)
            new_free = {(r, c) for r in range(5) for c in range(5)
                        if free[r][c]}
            if new_free - self.free_cells:
                self.free_cells |= new_free
                for r, c in new_free:
                    self._mark_daubed(r, c)
            # A clean later read can retract a stale free/wild ghost cell
            # that an earlier bad read left on the board.
            dropped = self.free_cells - new_free
            if dropped:
                self.free_cells -= dropped
                for r, c in sorted(dropped):
                    if self.daubed[r][c]:
                        self.daubed[r][c] = False
                        self.line_counts = B.line_counts(self.daubed)
                print("free/wild retracted: "
                      + ", ".join(f"r{r + 1}c{c + 1}"
                                  for r, c in sorted(dropped)))
            self.grid = grid
            self.card_scores = scores
            self.pending_card = None
            self.pending_card_n = 0
            self.popup = None
            self.last_grid_crop = frame[grid.y0:grid.y1,
                                        grid.x0:grid.x1].copy()
            self._capture_baseline(frame)
            self._set_card_visible(True)  # also restores the saved cells
            self.status("SCANNING", "watching for calls" + self._bingo_suffix(),
                        "green")
            return

        # A different card. The daub-aware read replays locked values into
        # any ticket that shows up, so a switch may only ever be decided by
        # a fresh-eyes re-read of the same frame that is nearly fully
        # readable. This killed a live bug where a new ticket locked with
        # the previous ticket's daub values grafted in.
        plain, plain_scores, plain_free = V.read_card_auto(
            self.engine, warped, daubed=daubed_cells, old=None)
        if (V.validate_card(plain, plain_free)
                or self._card_readable(plain) < ROUND_READABLE):
            # Fading card, popup, or half-dealt ticket: keep the saved round
            # and wait; recycled values must never replace a live round.
            print("card change not confirmed - keeping saved round")
            self.status("CARD HIDDEN", "waiting for card", "amber")
            return
        card, scores, free = plain, plain_scores, plain_free

        diff_cells = [(r, c) for r in range(5) for c in range(5)
                      if card[r][c] != self.card[r][c]]
        if len(diff_cells) < ROUND_CHANGE:
            # Same ticket: earlier reads misread a few digits. Patch the
            # values in place without resetting the live round state.
            if card == self.pending_card:
                self.pending_card_n += 1
            else:
                self.pending_card = card
                self.pending_card_n = 1
            if self.pending_card_n < 2:
                print("different card read - confirming before switching")
                self.status("SCANNING", "confirming new card...", "amber")
                return
            self.pending_card = None
            self.pending_card_n = 0
            fixes = ", ".join(
                f"r{r + 1}c{c + 1} "
                f"{self.card[r][c] if self.card[r][c] else '?'}->{card[r][c]}"
                for r, c in diff_cells)
            for r, c in diff_cells:
                self.card[r][c] = card[r][c]
            self.card_scores = scores
            print("card corrected: " + fixes)
            self.status("SCANNING", "card corrected", "green")
            # Ball calls that were scored NOT ON CARD against the broken
            # card may be on the card now: release and re-commit them.
            self._retry_missed()
            return

        # Confirmed big change: a real new round. Confirm once more before
        # replacing the round; one bad read must never produce highlights.
        if card == self.pending_card:
            self.pending_card_n += 1
        else:
            self.pending_card = card
            self.pending_card_n = 1

        if self.pending_card_n < 2:
            print("different card read - confirming before switching")
            self.status("SCANNING", "confirming new card...", "amber")
            return

        self.pending_card = None
        self.pending_card_n = 0

        # Confirmed new round.
        print("\nnew round -> card changed")
        self.grid = grid
        self.card = card
        self.card_scores = scores
        initial_daubs = self._seed_daubs(warped, free)
        self.last_calls.clear()
        self.not_on_card.clear()
        self.current_cells = []
        self.instant_cells.clear()
        self.instant_offered.clear()
        self.pending_cells.clear()
        self.pending = None
        self.pending_n = 0
        self.last_ball_crop = None
        self.pending_card = None
        self.pending_card_n = 0
        self.popup = None
        self.last_grid_crop = frame[grid.y0:grid.y1,
                                    grid.x0:grid.x1].copy()
        self._capture_baseline(frame)
        self.overlay.hide()
        self.card_visible = True
        self.status("SCANNING", "new round", "green")
        for row in card:
            print("  " + " ".join(f"{v:>3}" if v is not None else "  -" for v in row))
        extra_free = sorted(self.free_cells - {(2, 2)})
        if extra_free:
            print("extra free/wild: " + ", ".join(f"r{r + 1}c{c + 1}"
                                                  for r, c in extra_free))
        if initial_daubs:
            print("already daubed: " + ", ".join(f"r{r + 1}c{c + 1}"
                                                  for r, c in sorted(initial_daubs)))
        if self.initial_uncertain:
            print("daub state ambiguous - Pick Me Up will ask manually")
        print("constraints: OK")

    # ------------------------------------------------------------ loop

    def run(self) -> None:
        setup_logging()
        print(f"bingo helper v{VERSION}")

        if self.source_image is None:
            self.mutex = single_instance()
            if self.mutex is None:
                print("another instance is already running")
                return

        set_dpi_aware()
        # Must be created after DPI awareness, or Windows refuses to change it.
        self.overlay = Overlay()

        if not self.dry_run:
            self.bar = ControlBar(self._request_rescan, self._toggle_pause,
                                  self._request_quit)
            self.bar.set_status("STARTING", "loading vision...", "amber")
            self.bar.raise_()

        from rapidocr import RapidOCR
        print("loading OCR...")
        self.engine = RapidOCR()
        self.engine(np.full((64, 64, 3), 255, np.uint8),
                    use_det=False, use_cls=False, use_rec=True)

        if self.source_image is None:
            self.start_capture()
        else:
            print(f"offline image: {self.source_image.shape[1]}x{self.source_image.shape[0]}"
                  f"{' (dry run)' if self.dry_run else ''}")

        if not self.dry_run:
            self.status("WAITING FOR CARD", "keep the game visible", "amber")
        self.detect()

        register_hotkeys()
        if not self.dry_run:
            print("\nBar: Scan / Pause / Resume / Quit."
                  "  Hotkeys: F8 rescan, F9 overlay, F10 quit\n")
        else:
            print("\nF8 re-detect   F9 toggle overlay   F10 quit\n")

        loops = 0
        polls = 0
        next_poll = time.perf_counter()
        try:
            while self.running:
                loops += 1
                if self.max_frames and loops > self.max_frames:
                    break

                for kid in pump_messages():
                    if kid == 1:
                        self.detect()
                    elif kid == 2:
                        self.overlay_enabled = not self.overlay_enabled
                        if self.overlay_enabled and self.current_cells:
                            self.show_cells(*self.current_cells)
                        else:
                            self.overlay.hide()
                        print("overlay", "on" if self.overlay_enabled else "off")
                    elif kid == 3:
                        self.running = False

                if self.rescan_requested:
                    self.rescan_requested = False
                    self.detect()

                if not self.running:
                    break

                if self.paused:
                    time.sleep(0.03)
                    continue

                if self.grid is None:
                    if time.time() - self.last_detect_try > 0.6:
                        self.detect(quiet=self.detect_fail > 0)
                    time.sleep(0.05)
                    continue

                now = time.perf_counter()
                if now >= next_poll:
                    next_poll = now + 0.12
                    polls += 1
                    if self.max_polls and polls > self.max_polls:
                        break
                    frame = self.grab()
                    if frame is not None:
                        self._confirm_pending(frame)
                        self.check_round(frame)
                        self.poll_ball(frame)
                time.sleep(0.03)
        except KeyboardInterrupt:
            pass
        finally:
            self.cleanup()

    def cleanup(self) -> None:
        for kid in (1, 2, 3):
            try:
                user32.UnregisterHotKey(None, kid)
            except Exception:
                pass
        if self.bar is not None:
            try:
                self.bar.destroy()
            except Exception:
                pass
            self.bar = None
        try:
            self.overlay.destroy()
        except Exception:
            pass
        if self.cam is not None:
            try:
                self.cam.stop()
                self.cam.release()
            except Exception:
                pass
        if self.mutex:
            try:
                kernel32.CloseHandle(self.mutex)
            except Exception:
                pass
            self.mutex = None
        print("stopped")


if __name__ == "__main__":
    import argparse

    ap = argparse.ArgumentParser()
    ap.add_argument("--frames", type=int, default=0,
                    help="run this many loop iterations then exit (testing)")
    ap.add_argument("--polls", type=int, default=0,
                    help="run this many ball polls then exit (testing)")
    ap.add_argument("--image", help="test against a still image instead of the screen")
    ap.add_argument("--dry-run", action="store_true",
                    help="do not display the overlay (test only)")
    args = ap.parse_args()

    app = App()
    if args.image:
        img = cv2.imread(args.image)
        if img is None:
            raise SystemExit(f"cannot read image: {args.image}")
        app.source_image = img
    app.dry_run = args.dry_run
    app.max_frames = args.frames
    app.max_polls = args.polls
    app.run()
