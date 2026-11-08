"""Offline checks for v1.1 bingo math and power-up vision.

Run:  python test_v11.py
No screen capture, no overlay, no game input.
"""
from __future__ import annotations

import unittest
from pathlib import Path

import cv2

import bingo as B
import vision as V

ROOT = Path(__file__).resolve().parent
NORMAL = ROOT / "Screenshot 2026-11-06 095328.png"
INSTANT = (ROOT / "power up types with images examples"
           / "Power Up Instant Ball  Get 3 free balls at the same time "
             "Hint These balls will come and go quickly so be ready.png")
PICK = (ROOT / "power up types with images examples"
        / "Power Up Pick Me Up Choose any number to receive that ball "
          "The right number can unlock multiple bingos at the same time.png")


def values_1_to_25():
    return [[r * 5 + c + 1 for c in range(5)] for r in range(5)]


class BingoMathTests(unittest.TestCase):
    def test_fresh_round_only_centre_is_free(self):
        d = B.initial_daubed()
        self.assertTrue(d[2][2])
        self.assertEqual(sum(map(sum, d)), 1)
        self.assertEqual(B.completed_lines(d), [])

    def test_extra_free_cell_is_marked(self):
        d = B.initial_daubed({(1, 2)})
        self.assertTrue(d[1][2])
        self.assertTrue(d[2][2])
        self.assertEqual(sum(map(sum, d)), 2)

    def test_rows_columns_diagonals_corners(self):
        cells = {
            "row 1": ((0, 0), (0, 1), (0, 2), (0, 3), (0, 4)),
            "column 0": ((0, 0), (1, 0), (2, 0), (3, 0), (4, 0)),
            "diagonal": ((0, 0), (1, 1), (2, 2), (3, 3), (4, 4)),
            "four corners": ((0, 0), (0, 4), (4, 0), (4, 4)),
        }
        for name, line_cells in cells.items():
            d = B.initial_daubed()
            for r, c in line_cells:
                d[r][c] = True
            names = B.completed_lines(d)
            self.assertIn(name, names, msg=f"{name}: {names}")

    def test_pick_completes_two_lines(self):
        values = values_1_to_25()
        d = B.initial_daubed()
        # Candidate 1 at (0,0): fill the rest of row 1 and column 0.
        for c in range(1, 5):
            d[0][c] = True
        for r in range(1, 5):
            d[r][0] = True
        choices = B.rank_picks(values, d, [1, 25])
        self.assertTrue(choices)
        self.assertEqual(choices[0].number, 1)
        self.assertGreaterEqual(len(choices[0].completed), 2)

    def test_pick_one_away(self):
        values = values_1_to_25()
        d = B.initial_daubed()
        for c in (2, 3, 4):
            d[1][c] = True          # row 2 is one away; candidate at (1,0)
        choices = B.rank_picks(values, d, [6])
        self.assertEqual(len(choices), 1)
        self.assertIn("row 2", choices[0].one_away)

    def test_exact_tie_is_visible(self):
        values = values_1_to_25()
        d = B.initial_daubed()
        # Both candidates complete exactly one separate one-away row.
        for c in (1, 2, 3, 4):
            d[0][c] = True
        for c in (1, 2, 3, 4):
            d[4][c] = True
        choices = B.rank_picks(values, d, [1, 21])
        self.assertEqual(len(choices), 2)
        self.assertEqual(choices[0].score, choices[1].score)
        self.assertEqual(B.format_tie_choice(choices),
                         "tie 21/1 -> PICK 21")

    def test_centre_pick_is_skipped(self):
        values = values_1_to_25()
        d = B.initial_daubed()
        self.assertEqual(B.rank_picks(values, d, [13]), [])

    def test_half_dealt_card_is_rejected(self):
        values = [[None] * 5 for _ in range(5)]
        free = [[True] * 5 for _ in range(5)]
        issues = V.validate_card(values, free)
        self.assertTrue(any("too few readable numbers" in i for i in issues))

    def test_real_free_column_card_is_accepted(self):
        values = [
            [1, 16, None, 46, 61],
            [2, 17, None, 47, 62],
            [3, 18, None, 48, 63],
            [4, 19, None, 49, 64],
            [5, 20, None, 50, 65],
        ]
        free = [[False] * 5 for _ in range(5)]
        free[2][2] = True
        for r in range(5):
            free[r][2] = True
        self.assertEqual(V.validate_card(values, free), [])


class VisionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from rapidocr import RapidOCR
        cls.engine = RapidOCR()

    def test_normal_card_still_reads(self):
        img = cv2.imread(str(NORMAL))
        self.assertIsNotNone(img)
        grid = V.find_grid(img)
        self.assertIsNotNone(grid)
        warped = V.rectify(img, grid)
        values, _, free = V.read_card_ex(self.engine, warped)
        self.assertEqual(values[0][0], 4)
        self.assertEqual(values[4][4], 67)
        self.assertEqual(V.validate_card(values, free), [])
        self.assertEqual([(r, c) for r in range(5) for c in range(5) if free[r][c]],
                         [(2, 2)])

    def test_instant_card_has_second_free_cell(self):
        img = cv2.imread(str(INSTANT))
        self.assertIsNotNone(img)
        xs = [309 + int(i * (634 - 309) / 5) for i in range(6)]
        ys = [225 + int(i * (536 - 225) / 5) for i in range(6)]
        grid = V.Grid(309, 225, 634, 536, xs, ys)
        warped = V.rectify(img, grid)
        values, _, free = V.read_card_ex(self.engine, warped)
        self.assertEqual(V.validate_card(values, free), [])
        self.assertTrue(free[1][2])
        self.assertTrue(free[2][2])
        self.assertEqual(values[0][3], 46)
        self.assertEqual(values[1][3], 59)
        self.assertEqual(values[2][3], 57)
        self.assertEqual(values[2][4], 63)
        daubed, ambiguous = V.detect_daubed_cells(warped, free)
        self.assertFalse(ambiguous)
        self.assertEqual(len(daubed), 5)

    def test_normal_card_has_no_initial_daubs(self):
        img = cv2.imread(str(NORMAL))
        grid = V.find_grid(img)
        warped = V.rectify(img, grid)
        _, _, free = V.read_card_ex(self.engine, warped)
        daubed, ambiguous = V.detect_daubed_cells(warped, free)
        self.assertFalse(ambiguous)
        self.assertEqual(daubed, set())

    def test_instant_three_balls_read(self):
        img = cv2.imread(str(INSTANT))
        xs = [309 + int(i * (634 - 309) / 5) for i in range(6)]
        ys = [225 + int(i * (536 - 225) / 5) for i in range(6)]
        grid = V.Grid(309, 225, 634, 536, xs, ys)
        balls = V.find_balls(img, grid)
        self.assertEqual(len(balls), 3)
        nums = [V.read_ball_robust(self.engine, img, b,
                                   min_score=0.92)["number"] for b in balls]
        self.assertEqual(sorted(nums), [57, 59, 63])

    def test_fast_card_read_matches_reference_cards(self):
        # Normal card: the fast reader must return the same values as OCR.
        normal = cv2.imread(str(NORMAL))
        grid = V.find_grid(normal)
        warped = V.rectify(normal, grid)
        fast = V.read_card_fast(warped)
        self.assertIsNotNone(fast)
        values, scores, free = fast
        ocr_values, _ocr_scores, ocr_free = V.read_card_ex(self.engine, warped)
        self.assertEqual(V.validate_card(values, free), [])
        self.assertEqual(values, ocr_values)
        self.assertEqual(free, ocr_free)
        self.assertEqual(values[0][0], 4)
        self.assertEqual(values[4][4], 67)

        # Instant card has a second visually confirmed free/wild cell.
        img = cv2.imread(str(INSTANT))
        xs = [309 + int(i * (634 - 309) / 5) for i in range(6)]
        ys = [225 + int(i * (536 - 225) / 5) for i in range(6)]
        grid = V.Grid(309, 225, 634, 536, xs, ys)
        warped = V.rectify(img, grid)
        fast = V.read_card_fast(warped)
        self.assertIsNotNone(fast)
        values, scores, free = fast
        ocr_values, _ocr_scores, ocr_free = V.read_card_ex(self.engine, warped)
        self.assertEqual(V.validate_card(values, free), [])
        self.assertEqual(free, ocr_free)
        self.assertEqual([(r, c) for r in range(5) for c in range(5) if free[r][c]],
                         [(1, 2), (2, 2)])
        self.assertEqual(values, ocr_values)

    def test_instant_ball_layout_detected(self):
        # The real power-up screenshot itself must be recognized.
        img = cv2.imread(str(INSTANT))
        xs = [309 + int(i * (634 - 309) / 5) for i in range(6)]
        ys = [225 + int(i * (536 - 225) / 5) for i in range(6)]
        grid = V.Grid(309, 225, 634, 536, xs, ys)
        self.assertTrue(V.detect_instant_ball(img, grid))

        # A called-ball history row must not be recognized as Instant Ball.
        normal = cv2.imread(str(NORMAL))
        grid = V.find_grid(normal)
        self.assertFalse(V.detect_instant_ball(normal, grid))

    def test_pick_me_up_title_and_four_balls(self):
        img = cv2.imread(str(PICK))
        self.assertIsNotNone(img)
        found = V.find_popup_title(self.engine, img)
        self.assertIsNotNone(found)
        title, txt = found
        self.assertIn("PICK", txt.upper())
        bx, by, bw, bh = title
        H, W = img.shape[:2]
        pad = max(12, int(0.12 * bw))
        region = (bx - pad, by + bh - 10, bx + bw + pad,
                  min(H, by + bh + int(1.9 * bw)))
        balls = V.find_popup_balls(img, region)
        self.assertEqual(len(balls), 4)
        nums = [V.read_ball_robust(self.engine, img, b,
                                   min_score=0.92)["number"] for b in balls]
        self.assertEqual(sorted(nums), [10, 14, 37, 44])


if __name__ == "__main__":
    unittest.main(verbosity=2)
