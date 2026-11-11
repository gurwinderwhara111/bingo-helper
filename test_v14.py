"""Regression tests for the v1.14 fault-isolation patch.

Covers the exact crash class from bingo.log (8 mid-game deaths): OCR
returning garbage characters into a card/cell parse, and any unexpected
exception inside the watch loop.
"""
from __future__ import annotations

import sys
import unittest
from types import SimpleNamespace
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import app as A          # noqa: E402
import vision as V       # noqa: E402


class StubEngine:
    """RapidOCR-compatible stub: returns canned txts for every call."""

    def __init__(self, seq):
        self.seq = seq
        self.i = 0

    def __call__(self, img, use_det=False, use_cls=False, use_rec=True):
        t = self.seq[self.i % len(self.seq)]
        self.i += 1
        return SimpleNamespace(txts=(t,), scores=(0.9,))


def blank_card():
    return np.full((V.CANON * 5, V.CANON * 5, 3), 255, np.uint8)


class CrashClass(unittest.TestCase):
    """The '²'/'�'/'?3' crash class: OCR garbage must never raise."""

    def test_read_card_garbage_no_crash(self):
        eng = StubEngine(["²", "⑤", "?3", "", "1", "58", "1²", "©"])
        values, scores = V.read_card(eng, blank_card())
        for r in range(5):
            for c in range(5):
                v = values[r][c]
                if v is not None:
                    lo, hi = V.COL_RANGES[c]
                    self.assertTrue(lo <= v <= hi, f"{r},{c}={v}")

    def test_read_card_still_parses_good_digits(self):
        # 24 cells, cycling a valid in-range number per column
        seq = (["5", "20", "40", "55", "70"] * 5)[:24]
        eng = StubEngine(seq)
        values, _ = V.read_card(eng, blank_card())
        got = [values[r][c] for r in range(5) for c in range(5)
               if not (r == 2 and c == 2)]
        self.assertIn(5, got)
        self.assertIn(70, got)

    def test_ball_reader_garbage_no_crash(self):
        eng = StubEngine(["²", "©", "?3"])
        img = np.full((40, 40, 3), 255, np.uint8)
        bbox = (0, 0, 40, 40)
        out = V.read_ball_robust(eng, img, bbox, min_score=0.92)
        self.assertIsNone(out["number"])


class LoopIsolation(unittest.TestCase):
    """One raising frame must never kill the watcher."""

    def _app(self):
        application = A.App()
        application.dry_run = True

        class StubOverlay:
            def hide(self):
                pass

            def show(self, *a, **k):
                pass

        application.overlay = StubOverlay()
        return application

    def test_poll_once_survives_raising_poll_ball(self):
        application = self._app()

        def boom(frame):
            raise RuntimeError("engine exploded")

        application._confirm_pending = lambda frame: None
        application.check_round = lambda frame: None
        application.poll_ball = boom
        application._poll_once(np.zeros((8, 8, 3), np.uint8))
        self.assertEqual(application.crash_count, 1)

    def test_poll_once_survives_raising_check_round(self):
        application = self._app()

        def boom(frame):
            raise ValueError("bad frame")

        application._confirm_pending = lambda frame: None
        application.check_round = boom
        application.poll_ball = lambda frame: None
        application._poll_once(np.zeros((8, 8, 3), np.uint8))
        self.assertEqual(application.crash_count, 1)

    def test_safe_detect_survives_raising_detect(self):
        application = self._app()

        def boom(quiet=False):
            raise RuntimeError("detect blew up")

        application.detect = boom
        application._safe_detect()
        self.assertEqual(application.crash_count, 1)

    def test_guard_throttle(self):
        application = self._app()
        n_calls = [0]

        def boom(frame):
            n_calls[0] += 1
            raise RuntimeError("x")

        application._confirm_pending = lambda frame: None
        application.check_round = lambda frame: None
        application.poll_ball = boom
        for _ in range(5):
            application._poll_once(np.zeros((8, 8, 3), np.uint8))
        self.assertEqual(application.crash_count, 5)


if __name__ == "__main__":
    unittest.main(verbosity=2)
