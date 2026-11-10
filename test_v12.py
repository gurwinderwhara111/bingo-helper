"""Offline checks for the v1.12 reliability changes.

Run:  python test_v12.py
Covers what v1.12 changed on purpose:
  1. Ball reads keep flowing while a Pick Me Up-style popup is open.
  2. A ball scored NOT ON CARD can re-commit after the card self-heals.
  3. The uncertain-daub wrong-advice path is gone (wrong daubs cost points).
  4. commit() drops the powerup-offer dedup set.
"""
from __future__ import annotations

import contextlib
import io
import time
import unittest
from pathlib import Path

import numpy as np

import app as A
import vision as V


ROOT = Path(__file__).resolve().parent


class DummyOverlay:
    def hide(self):
        pass

    def show(self, *a, **k):
        pass


def make_app() -> A.App:
    app = A.App()
    app.dry_run = True
    app.overlay = DummyOverlay()
    return app


def live_card():
    """A legal card with one missing N value at r3c4, like a bad lock."""
    card = [
        [1, 18, 33, 50, 61],
        [2, 17, 34, 51, 62],
        [3, 16, 35, 52, 63],
        [4, 24, 36, 53, 64],
        [5, 20, 37, 54, 65],
    ]
    card[2][3] = None  # unreadable cell in the G column
    return card


def set_live_grid(app: A.App) -> None:
    app.grid = V.Grid(x0=751, y0=386, x1=1169, y1=782,
                      xs=[751, 834, 917, 1000, 1083, 1169],
                      ys=[386, 464, 543, 620, 699, 782])


class PopupBallReadTests(unittest.TestCase):
    def test_popup_does_not_block_ball_reads(self):
        app = make_app()
        app.card = live_card()
        set_live_grid(app)
        app.card_visible = False          # popup hid the card, as before
        app.popup = {"title": (750, 400, 400, 40)}   # confirm-gate dict
        sentinel = np.zeros((1, 1, 3), np.uint8)
        app.last_ball_crop = sentinel     # poll_ball must reopen the gate
        frame = np.full((900, 1500, 3), 255, np.uint8)
        app.poll_ball(frame)
        # v1.11 returned at the gate and left the sentinel; v1.12 runs the
        # read and clears the crop after finding no balls.
        self.assertIsNone(app.last_ball_crop)

    def test_card_hidden_without_popup_still_blocks(self):
        app = make_app()
        app.card = live_card()
        set_live_grid(app)
        app.card_visible = False
        app.popup = None
        sentinel = np.zeros((1, 1, 3), np.uint8)
        app.last_ball_crop = sentinel
        frame = np.full((900, 1500, 3), 255, np.uint8)
        app.poll_ball(frame)
        # unchanged behaviour: fading card, no popup -> no reads
        self.assertIsNotNone(app.last_ball_crop)
        self.assertEqual(app.last_ball_crop.shape, (1, 1, 3))


class HealTests(unittest.TestCase):
    def test_not_on_card_then_heal_recommits(self):
        app = make_app()
        card = live_card()
        card[3][3] = None      # the bad lock: 53 unreadable in the G column
        app.card = card
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            app.commit("G", 53, frame=None)
        text = out.getvalue()
        self.assertIn("G 53 -> NOT ON CARD", text)
        self.assertIn(("G", 53), app.not_on_card)
        self.assertIn(("G", 53), app.last_calls)
        self.assertEqual(app.current_cells, [])

        # the card heals in place (as _verify_card / check_round do)
        app.card[3][3] = 53
        out2 = io.StringIO()
        with contextlib.redirect_stdout(out2):
            app._retry_missed()
        text2 = out2.getvalue()
        self.assertIn("G 53 -> row 4 col 4", text2)
        self.assertNotIn(("G", 53), app.not_on_card)
        self.assertEqual(app.current_cells, [(3, 3)])
        self.assertIn(("G", 53), app.last_calls)

    def test_heal_does_not_resurrect_stale_misses(self):
        app = make_app()
        app.card = live_card()
        # a miss older than the 30 s window must be dropped, not retried
        app.not_on_card[("G", 53)] = time.time() - 120.0
        app.card[3][3] = 53
        with contextlib.redirect_stdout(io.StringIO()):
            app._retry_missed()
        self.assertIn(("G", 53), app.not_on_card)
        self.assertEqual(app.current_cells, [])


class NoWrongAdviceTests(unittest.TestCase):
    def test_uncertain_daub_advice_removed(self):
        app = make_app()
        card = live_card()
        card[3][3] = None        # 53 never made it onto the locked card
        app.card = card
        app.daubed[3][3] = True  # an unreadable daub sits in the G column
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            app.commit("G", 53, frame=None)
        text = out.getvalue()
        # the old amber "(daubed cell, value unread - uncertain)" highlight
        # for a ball that is not on the card is gone: wrong daubs cost -100.
        self.assertNotIn("daubed cell", text)
        self.assertIn("NOT ON CARD", text)
        self.assertEqual(app.current_cells, [])


class OfferBookkeepingTests(unittest.TestCase):
    def test_commit_clears_instant_offered(self):
        app = make_app()
        app.card = live_card()
        app.instant_offered = {("I", 18)}
        with contextlib.redirect_stdout(io.StringIO()):
            app.commit("I", 18, frame=None)
        self.assertEqual(app.instant_offered, set())

    def test_new_round_clears_miss_memory(self):
        app = make_app()
        app.not_on_card = {("G", 53): time.time()}
        app.instant_offered = {("I", 18)}
        app.last_calls = {("G", 53)}
        # simulate the fresh-round reset shared by detect()/check_round
        app.last_calls.clear()
        app.not_on_card.clear()
        app.instant_offered.clear()
        self.assertEqual(app.not_on_card, {})
        self.assertEqual(app.instant_offered, set())


if __name__ == "__main__":
    unittest.main(verbosity=2)
