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

import vision as V
from controls import ControlBar
from overlay import Overlay

logging.disable(logging.INFO)

VK_F8, VK_F9, VK_F10 = 0x77, 0x78, 0x79
WM_HOTKEY = 0x0312
PM_REMOVE = 0x0001

CELL_CMP = 48          # size used to compare one cell with its round baseline
CELL_DIFF = 12.0       # mean per-pixel diff that counts as "this cell changed"
ROUND_CHANGE = 12      # changed cells that mean "card covered or re-dealt"
ROUND_CHECK = 0.3      # seconds between cheap cell-change checks

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
        self.last_call: tuple[str, int] | None = None
        self.current_cell: tuple[int, int] | None = None
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
            restore = (self.grid is not None and self.current_cell is not None
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
                    self.current_cell = None
                    self._set_card_visible(False)
                    self.last_round_check = 0.0
                    self.last_round_ocr = 0.0
                    self.status("SCANNING", "card changed - checking", "amber")
                    print("resumed (card changed)")
                    return

            # Restore the last confirmed cell. If a new ball was called while
            # paused, the next poll replaces this within ~0.25 s.
            self._set_card_visible(True)
            if restore:
                self.show_cell(*self.current_cell)
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
        self.last_call = None
        self.current_cell = None
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
        self.overlay.hide()

    def _set_card_visible(self, visible: bool) -> None:
        """Track whether the saved card is on screen and trusted."""
        if visible == self.card_visible:
            return
        self.card_visible = visible
        if visible:
            print("card visible - resuming")
            self.status("SCANNING", "watching for calls", "green")
            if self.current_cell is not None and self.overlay_enabled:
                self.show_cell(*self.current_cell)
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

    def _changed_cells(self, frame, grid=None) -> int:
        """How many of the 25 cells differ from the saved round baseline."""
        g = grid if grid is not None else self.grid
        if g is None or self.cell_baseline is None:
            return 25
        crops = []
        for r in range(5):
            for c in range(5):
                cell = frame[g.ys[r]:g.ys[r + 1], g.xs[c]:g.xs[c + 1]]
                if cell.size == 0:
                    return 25
                crops.append(cv2.resize(cell, (CELL_CMP, CELL_CMP),
                                        interpolation=cv2.INTER_AREA))
        cur = np.stack(crops).astype(np.int16)
        diff = np.abs(cur - self.cell_baseline).mean(axis=(1, 2, 3))
        return int((diff > CELL_DIFF).sum())

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

        warped = V.rectify(frame, grid)
        card, scores = V.read_card(self.engine, warped)
        issues = V.validate_card(card)
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

        # Valid card: lock this round. All later calls use this saved copy.
        self.grid = grid
        self.card = card
        self.card_scores = scores
        self.last_call = None
        self.current_cell = None
        self.pending = None
        self.pending_n = 0
        self.last_ball_crop = None
        self.pending_card = None
        self.pending_card_n = 0
        self.last_round_check = 0.0
        self.last_round_ocr = 0.0
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

    def poll_ball(self, frame) -> None:
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

        bbox = V.find_ball(crop, self._sub_grid(x0, y0))
        if bbox is None:
            self.pending = None
            self.pending_n = 0
            self.last_ball_crop = None  # keep looking every poll
            return
        bbox = (bbox[0] + x0, bbox[1] + y0, bbox[2], bbox[3])

        ball = V.read_ball(self.engine, frame, bbox)
        num = ball["number"]
        if num is None or not (1 <= num <= 75) or ball["number_score"] < 0.90:
            self.last_ball_crop = None  # retry the read, keep any pending call
            return
        letter = ball["letter"]
        derived = V.letter_for(num)
        if letter and derived != letter:
            print(f"reject: ball says {letter} {num}, range says {derived}")
            self.last_ball_crop = None
            return
        if not letter:
            letter = derived or "?"

        key = (letter, num)
        if key == self.pending:
            self.pending_n += 1
        else:
            self.pending = key
            self.pending_n = 1
            self.pending_t0 = time.perf_counter()
        if self.pending_n < 2:
            return
        if key == self.last_call:
            self.pending_n = 0
            return

        self.last_call = key
        self.pending_n = 0
        self.commit(letter, num)

    def commit(self, letter: str, num: int) -> None:
        ms = (time.perf_counter() - self.pending_t0) * 1000 if self.pending_t0 else 0.0
        self.pending_t0 = 0.0
        pos = V.locate_number(self.card, num)
        if pos is None:
            print(f"{letter} {num} -> NOT ON CARD  ({ms:.0f} ms)")
            self.current_cell = None
            self.overlay.hide()
            self.status("SCANNING" if self.card_visible else "CARD HIDDEN",
                        f"{letter} {num} -> not on card",
                        "green" if self.card_visible else "amber")
            return

        r, c = pos
        self.current_cell = pos
        print(f"{letter} {num} -> row {r + 1} col {c + 1}  ({ms:.0f} ms)")
        self.status("SCANNING" if self.card_visible else "CARD HIDDEN",
                    f"{letter} {num} -> row {r + 1} col {c + 1}",
                    "green" if self.card_visible else "amber")
        if self.card_visible and self.overlay_enabled:
            self.show_cell(r, c)

    # ------------------------------------------------------------ overlay

    def _cell_rect(self, r: int, c: int):
        g = self.grid
        x0, y0 = g.xs[c], g.ys[r]
        return (x0, y0, g.xs[c + 1] - x0, g.ys[r + 1] - y0)

    def show_cell(self, r: int, c: int) -> None:
        if not self.card_visible:
            return
        if self.dry_run:
            print(f"overlay hole -> grid={self.grid.rect} cell={self._cell_rect(r, c)}")
            return
        self.overlay.show(self.grid.rect, self._cell_rect(r, c), dim_alpha=235)
        if self.bar is not None:
            self.bar.raise_()

    # ------------------------------------------------------------ round state

    def check_round(self, frame) -> None:
        """Keep the saved round. Re-read only when the card itself changes."""
        now = time.time()
        if now - self.last_round_check < ROUND_CHECK:
            return
        self.last_round_check = now

        if frame is None or self.grid is None or self.card is None:
            return

        # Locate the grid every check. It is cheap, and it stops 1-pixel
        # capture jitter from looking like a whole new round. Compare the
        # baseline at the current grid, not the old one.
        new_grid = V.find_grid(frame)
        grid = new_grid if new_grid is not None else self.grid
        changed = self._changed_cells(frame, grid)

        if changed < ROUND_CHANGE:
            # Normal daubs/hover, or a popup just cleared. The saved card is
            # still the round. Do not OCR; do not throw anything away.
            if new_grid is not None and self._grid_moved(self.grid, new_grid):
                self.grid = new_grid
                self.last_ball_crop = None
                self.pending = None
                self.pending_n = 0
                self._capture_baseline(frame)
                if (self.card_visible and self.current_cell
                        and self.overlay_enabled):
                    self.show_cell(*self.current_cell)
            self.last_grid_crop = frame[self.grid.y0:self.grid.y1,
                                        self.grid.x0:self.grid.x1].copy()
            if not self.card_visible:
                self._set_card_visible(True)
            return

        # Many cells changed: a popup is covering the card, the card moved,
        # or a new round was dealt. Never dim a popup and never guess.
        was_visible = self.card_visible
        self._set_card_visible(False)

        cooldown = 0.6 if self.pending_card is not None else 1.0
        if not was_visible and now - self.last_round_ocr < cooldown:
            return
        self.last_round_ocr = now

        # OCR only when the cells really changed.
        warped = V.rectify(frame, grid)
        card, scores = V.read_card(self.engine, warped)

        if V.validate_card(card):
            # Covered, mid-deal, or unreadable. Keep the saved round and wait.
            self.status("CARD HIDDEN", "waiting for card", "amber")
            return

        if card == self.card:
            # Same card: it moved, was daubed a lot, or the popup cleared.
            self.grid = grid
            self.card_scores = scores
            self.pending_card = None
            self.pending_card_n = 0
            self.last_grid_crop = frame[grid.y0:grid.y1,
                                        grid.x0:grid.x1].copy()
            self._capture_baseline(frame)
            self._set_card_visible(True)  # also restores the saved cell
            self.status("SCANNING", "watching for calls", "green")
            return

        # A different card. Confirm it once more before replacing the round:
        # one bad read must never produce wrong highlights.
        if card == self.pending_card:
            self.pending_card_n += 1
        else:
            self.pending_card = card
            self.pending_card_n = 1

        if self.pending_card_n < 2:
            print("different card read - confirming before switching")
            self.status("SCANNING", "confirming new card...", "amber")
            return

        # Confirmed new round.
        print("\nnew round -> card changed")
        self.grid = grid
        self.card = card
        self.card_scores = scores
        self.last_call = None
        self.current_cell = None
        self.pending = None
        self.pending_n = 0
        self.last_ball_crop = None
        self.pending_card = None
        self.pending_card_n = 0
        self.last_grid_crop = frame[grid.y0:grid.y1,
                                    grid.x0:grid.x1].copy()
        self._capture_baseline(frame)
        self.overlay.hide()
        self.card_visible = True
        self.status("SCANNING", "new round", "green")
        for row in card:
            print("  " + " ".join(f"{v:>3}" if v is not None else "  -" for v in row))
        print("constraints: OK")

    # ------------------------------------------------------------ loop

    def run(self) -> None:
        setup_logging()

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
                        if self.overlay_enabled and self.current_cell:
                            self.show_cell(*self.current_cell)
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
                        self.poll_ball(frame)
                        self.check_round(frame)
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
