"""Bingo vision core: grid locate -> rectify -> read card -> read ball -> decide.

Everything here is pure computer vision. No capture, no overlay, no game access.
"""
from __future__ import annotations

import math
from functools import lru_cache
from pathlib import Path

import cv2
import numpy as np
from dataclasses import dataclass
from typing import Optional

COL_RANGES = ((1, 15), (16, 30), (31, 45), (46, 60), (61, 75))
LETTERS = ("B", "I", "N", "G", "O")
CANON = 500  # rectified grid is CANON x CANON
BASE_DIR = Path(__file__).resolve().parent
DIGIT_TEMPLATES_FILE = BASE_DIR / "assets" / "digit_templates.npz"
FAST_DIGIT_MIN_SCORE = 0.82


# ---------------------------------------------------------------- geometry

@dataclass
class Grid:
    x0: int
    y0: int
    x1: int
    y1: int
    xs: list[int]
    ys: list[int]

    @property
    def width(self) -> int:
        return self.x1 - self.x0

    @property
    def height(self) -> int:
        return self.y1 - self.y0

    @property
    def rect(self) -> tuple[int, int, int, int]:
        return self.x0, self.y0, self.x1, self.y1


def _collapse(vals, tol: int = 3):
    out: list[list[int]] = []
    for v in vals:
        if out and v - out[-1][-1] <= tol:
            out[-1].append(v)
        else:
            out.append([v])
    return [int(round(sum(g) / len(g))) for g in out]


def _pick_even(coords, n: int = 6, gap_tol: int = 10, min_gap: int = 25):
    """Pick n consecutive coords whose gaps are near-equal."""
    best = None
    for i in range(len(coords) - n + 1):
        w = coords[i:i + n]
        gaps = [w[j + 1] - w[j] for j in range(n - 1)]
        if min(gaps) < min_gap:
            continue
        spread = max(gaps) - min(gaps)
        if spread > gap_tol:
            continue
        score = (spread, -w[-1])
        if best is None or score < best[0]:
            best = (score, w)
    return best[1] if best else None


def find_header(img):
    """Locate the coloured B/I/N/G/O strip: 3+ saturated blocks in one row."""
    hsv = cv2.cvtColor(img, cv2.COLOR_BGR2HSV)
    sat = (((hsv[:, :, 1] > 110) & (hsv[:, :, 2] > 70)) * 255).astype(np.uint8)
    n, _, st, _ = cv2.connectedComponentsWithStats(sat, 8)
    comps = []
    for i in range(1, n):
        x, y, w, h, area = st[i]
        if area > 500 and 15 < h < 70 and w > 40:
            comps.append((int(x), int(y), int(w), int(h)))
    if not comps:
        return None
    comps.sort(key=lambda c: c[1] + c[3] / 2)
    groups: list[list] = []
    for c in comps:
        yc = c[1] + c[3] / 2
        if groups and abs(yc - groups[-1][0]) < 12:
            groups[-1][1].append(c)
        else:
            groups.append([yc, [c]])
    best = None
    for _, g in groups:
        if len(g) < 3:
            continue
        x0 = min(c[0] for c in g)
        x1 = max(c[0] + c[2] for c in g)
        if x1 - x0 < 200:
            continue
        # Prefer the full five-letter header. A row of brightly daubed cells
        # can also form a wide group; the real header is above it and has all
        # five blocks. This is what kept Instant Ball cards from being found.
        y0 = min(c[1] for c in g)
        score = (len(g), x1 - x0, -y0)
        if best is None or score > best[0]:
            best = (score, x0, y0, x1, max(c[1] + c[3] for c in g))
    if best is None:
        return None
    return best[1], best[2], best[3], best[4]


def _even_span(a: int, b: int, n: int = 6) -> list[int]:
    """Six evenly spaced coordinates from a to b, inclusive."""
    return [int(round(a + (b - a) * i / (n - 1))) for i in range(n)]


def find_grid(img) -> Optional[Grid]:
    header = find_header(img)
    if header is None:
        return None
    hx0, _, hx1, hy1 = header
    H, W = img.shape[:2]

    g = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    bw = cv2.adaptiveThreshold(g, 255, cv2.ADAPTIVE_THRESH_MEAN_C,
                               cv2.THRESH_BINARY_INV, 25, 10)

    # --- horizontal boundaries; the header's bottom edge is the grid's top
    hw = max(1, hx1 - hx0)
    kh = max(60, int(0.30 * hw))
    hm = cv2.morphologyEx(bw, cv2.MORPH_OPEN,
                          cv2.getStructuringElement(cv2.MORPH_RECT, (kh, 1)))
    y_end = min(H, hy1 + int(1.6 * hw))
    rows = [y for y in range(max(0, hy1 - 2), y_end)
            if int((hm[y, hx0:hx1] > 0).sum()) > 0.45 * hw]
    hp = [p for p in _collapse(rows) if p > hy1 + 10]

    ys = _pick_even([hy1] + hp, 6) or _pick_even(hp, 6)

    # --- vertical boundaries. When several cells are daubed, grid lines can
    # be covered and neither set of internal lines is complete. Search a
    # slightly larger window, then infer the missing spacing from the outer
    # edges instead of giving up on the whole card.
    if ys is not None:
        gy0, gy1 = ys[0], ys[-1]
        gh = max(1, gy1 - gy0)
        kv = max(40, int(0.14 * gh))
        col_frac = 0.45
    else:
        gy0 = hy1
        gy1 = min(H, hy1 + int(1.25 * hw))
        gh = max(1, gy1 - gy0)
        kv = max(20, int(0.10 * gh))
        col_frac = 0.40

    vm = cv2.morphologyEx(bw, cv2.MORPH_OPEN,
                          cv2.getStructuringElement(cv2.MORPH_RECT, (1, kv)))
    x_lo, x_hi = max(0, hx0 - 20), min(W, hx1 + 20)
    cols = [x for x in range(x_lo, x_hi)
            if int((vm[gy0:gy1, x] > 0).sum()) > col_frac * gh]
    cols = _collapse(cols)
    xs = _pick_even(cols, 6)

    if xs is None:
        # Keep only candidates near the header ends, then infer five equal
        # columns between them. Internal line detection may have missed a few.
        margin = max(20, int(0.12 * hw))
        lefts = [x for x in cols if abs(x - hx0) <= margin]
        rights = [x for x in cols if abs(x - hx1) <= margin]
        left = min(lefts, key=lambda x: abs(x - hx0)) if lefts else hx0
        right = max(rights, key=lambda x: abs(x - hx1)) if rights else hx1
        if right - left >= 0.6 * hw:
            xs = _even_span(left, right)
        else:
            xs = _even_span(hx0, hx1)

    if ys is None:
        # The card is five equal rows. Use the width as the scale and prefer
        # a real bottom border when one survived the daubs.
        expected_h = int(round(0.94 * (xs[-1] - xs[0])))
        bottom = None
        for p in hp:
            if abs((p - hy1) - expected_h) <= max(12, int(0.10 * expected_h)):
                bottom = p
                break
        if bottom is None:
            bottom = min(H - 1, hy1 + expected_h)
        if bottom <= hy1 + 10:
            return None
        ys = _even_span(hy1, bottom)

    if (xs[0] < 0 or ys[0] < 0 or xs[-1] >= W or ys[-1] >= H
            or xs[-1] - xs[0] < 100 or ys[-1] - ys[0] < 100):
        return None

    # A bingo card's 5x5 grid is essentially square. Reject a loose
    # header-like strip or a partial bottom UI panel before it can be treated
    # as a card.
    ratio = (ys[-1] - ys[0]) / max(1, xs[-1] - xs[0])
    if not 0.75 <= ratio <= 1.15:
        return None

    return Grid(x0=xs[0], y0=ys[0], x1=xs[5], y1=ys[5], xs=xs, ys=ys)


def rectify(img, grid: Grid, size: int = CANON):
    src = np.float32([[grid.x0, grid.y0], [grid.x1, grid.y0],
                      [grid.x1, grid.y1], [grid.x0, grid.y1]])
    dst = np.float32([[0, 0], [size, 0], [size, size], [0, size]])
    m = cv2.getPerspectiveTransform(src, dst)
    return cv2.warpPerspective(img, m, (size, size), flags=cv2.INTER_CUBIC)


def cell_img(warped, r: int, c: int, inset: float = 0.08, size: int = CANON):
    cw = size / 5.0
    x0 = int(c * cw + cw * inset)
    x1 = int((c + 1) * cw - cw * inset)
    y0 = int(r * cw + cw * inset)
    y1 = int((r + 1) * cw - cw * inset)
    return warped[y0:y1, x0:x1]


# ---------------------------------------------------------------- glyphs

def glyph_mask(cell):
    """Binary 32x32 ink mask, scale/translation normalised."""
    g = cv2.cvtColor(cell, cv2.COLOR_BGR2GRAY)
    _, th = cv2.threshold(g, 0, 255, cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU)
    h, w = th.shape
    m = max(3, int(0.06 * min(h, w)))
    th[:m, :] = 0
    th[-m:, :] = 0
    th[:, :m] = 0
    th[:, -m:] = 0
    ys, xs = np.where(th > 0)
    if len(xs) < 20:
        return None
    crop = th[ys.min():ys.max() + 1, xs.min():xs.max() + 1]
    h, w = crop.shape
    s = max(h, w)
    pad = np.zeros((s, s), np.uint8)
    pad[(s - h) // 2:(s - h) // 2 + h, (s - w) // 2:(s - w) // 2 + w] = crop
    return cv2.resize(pad, (32, 32), interpolation=cv2.INTER_AREA)


def split_digits(cell, count: Optional[int] = None):
    """Segment a cell's number into individual 32x32 digit masks."""
    g = cv2.cvtColor(cell, cv2.COLOR_BGR2GRAY)
    _, th = cv2.threshold(g, 0, 255, cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU)
    h, w = th.shape
    m = max(3, int(0.06 * min(h, w)))
    th[:m, :] = 0
    th[-m:, :] = 0
    th[:, :m] = 0
    th[:, -m:] = 0

    col = (th > 0).sum(axis=0)
    runs, inrun, start = [], False, 0
    for x, c in enumerate(col):
        if c > 0 and not inrun:
            start, inrun = x, True
        elif c == 0 and inrun:
            runs.append([start, x])
            inrun = False
    if inrun:
        runs.append([start, len(col)])

    merged: list[list[int]] = []
    for r in runs:
        if merged and r[0] - merged[-1][1] <= 1:
            merged[-1][1] = r[1]
        else:
            merged.append(r)

    if count and len(merged) != count:
        ys, xs = np.where(th > 0)
        if len(xs) == 0:
            return []
        x0, x1 = int(xs.min()), int(xs.max()) + 1
        if count == 1:
            merged = [[x0, x1]]
        else:
            step = (x1 - x0) / count
            merged = [[int(x0 + i * step), int(x0 + (i + 1) * step)] for i in range(count)]

    out = []
    for x0, x1 in merged:
        sub = th[:, x0:x1]
        ys, xs = np.where(sub > 0)
        if len(xs) == 0:
            continue
        crop = sub[ys.min():ys.max() + 1, xs.min():xs.max() + 1]
        hh, ww = crop.shape
        s = max(hh, ww)
        pad = np.zeros((s, s), np.uint8)
        pad[(s - hh) // 2:(s - hh) // 2 + hh, (s - ww) // 2:(s - ww) // 2 + ww] = crop
        out.append(cv2.resize(pad, (32, 32), interpolation=cv2.INTER_AREA))
    return out


def ncc(a, b) -> float:
    a = a.astype(np.float32).ravel()
    b = b.astype(np.float32).ravel()
    a -= a.mean()
    b -= b.mean()
    d = np.linalg.norm(a) * np.linalg.norm(b)
    return float((a * b).sum() / d) if d > 1e-6 else 0.0


# ---------------------------------------------------------------- fast digits

@lru_cache(maxsize=2)
def load_digit_templates(path: str = "") -> Optional[tuple[np.ndarray, tuple[str, ...]]]:
    """Load pre-extracted digit templates for fast card reading.

    The template file is generated from the bundled reference cards, so no OCR
    is needed at runtime.  A missing or malformed file is not fatal; callers
    fall back to the OCR path.
    """
    file = Path(path) if path else DIGIT_TEMPLATES_FILE
    try:
        data = np.load(file, allow_pickle=False)
        templates = np.asarray(data["templates"], dtype=np.float32)
        labels = tuple(str(x) for x in data["labels"].tolist())
    except Exception:
        return None
    if templates.ndim != 2 or templates.shape[1] != 1024 or not labels:
        return None
    if templates.shape[0] != len(labels):
        return None
    return templates, labels


def _template_vector(mask) -> np.ndarray:
    vec = mask.astype(np.float32).ravel()
    vec -= vec.mean()
    norm = np.linalg.norm(vec)
    if norm > 1e-6:
        vec /= norm
    return vec


def match_digit_mask(mask, templates: tuple[np.ndarray, tuple[str, ...]]) -> tuple[Optional[str], float]:
    """Return the best digit label and score for one 32x32 digit mask."""
    mat, labels = templates
    if mat.size == 0:
        return None, 0.0
    scores = mat @ _template_vector(mask)
    idx = int(scores.argmax())
    label = labels[idx]
    return label, float(scores[idx])


def read_card_fast(warped, templates=None, size: int = CANON,
                   daubed=None, old=None):
    """Read a 5x5 card using digit templates instead of OCR.

    Returns the same shape as ``read_card_ex``.  Any unreadable or
    out-of-column number is left as ``None`` so ``validate_card`` can reject
    it before an overlay is ever shown.

    ``daubed`` plus ``old`` carry a locked round's knowledge: known-daubed
    number cells reuse their saved value and skip template matching, so an
    orange daub fill can never mark them as free/wild cells.
    """
    if templates is None:
        templates = load_digit_templates()
    if templates is None:
        return None

    values = [[None] * 5 for _ in range(5)]
    scores = [[0.0] * 5 for _ in range(5)]
    free = [[False] * 5 for _ in range(5)]
    free[2][2] = True  # standard 75-ball FREE space

    for r in range(5):
        for c in range(5):
            if r == 2 and c == 2:
                continue
            daub_guard = bool(daubed) and (r, c) in daubed
            if (daub_guard and old is not None
                    and old[r][c] is not None):
                # A pixel-confirmed daub keeps its locked number: redeciding
                # it from under the orange fill recreated fake new rounds.
                values[r][c] = old[r][c]
                scores[r][c] = 0.90
                continue
            cell = cell_img(warped, r, c, size=size)

            masks = split_digits(cell)
            if len(masks) not in (1, 2):
                masks = []

            digits: list[str] = []
            digit_scores: list[float] = []
            for mask in masks:
                digit, score = match_digit_mask(mask, templates)
                if digit is None or score < FAST_DIGIT_MIN_SCORE:
                    digits = []
                    break
                digits.append(digit)
                digit_scores.append(score)

            number = 0
            if digits:
                number = int("".join(digits))
                lo, hi = COL_RANGES[c]
                if not (1 <= number <= 75 and lo <= number <= hi):
                    number = 0

            if not digits or not number:
                # A daubed cell with no locked value is unknown: the orange
                # fill is a daub, not a pumpkin, so it is never a free cell.
                if daub_guard:
                    continue
                # Only a cell that cannot be read as a number may be a
                # pumpkin/free cell.  A just-daubed number cell must stay a
                # number, or a whole card can look "free".
                if is_free_cell(cell):
                    free[r][c] = True
                    scores[r][c] = 1.0
                continue

            values[r][c] = number
            scores[r][c] = min(digit_scores)

    return values, scores, free


def read_card_auto(engine, warped, size: int = CANON, daubed=None, old=None):
    """Fast template card read, with an OCR fallback for safety."""
    fast = read_card_fast(warped, size=size, daubed=daubed, old=old)
    if fast is not None:
        values, scores, free = fast
        issues = validate_card(values, free)
        good = [scores[r][c] for r in range(5) for c in range(5)
                if not free[r][c] and values[r][c] is not None]
        if not issues and good and min(good) >= FAST_DIGIT_MIN_SCORE:
            return values, scores, free
    return read_card_ex(engine, warped, size=size, daubed=daubed, old=old)


# ---------------------------------------------------------------- OCR

def ocr(engine, img, scale: float = 1.0):
    if scale != 1.0:
        img = cv2.resize(img, None, fx=scale, fy=scale, interpolation=cv2.INTER_CUBIC)
    out = engine(img, use_det=False, use_cls=False, use_rec=True)
    txts = getattr(out, "txts", None)
    scores = getattr(out, "scores", None)
    if not txts:
        return "", 0.0
    return str(txts[0]), float(scores[0]) if scores else 0.0


def is_free_cell(cell, min_ratio: float = 0.28) -> bool:
    """True when a cell is the orange pumpkin/free-space artwork.

    The centre free space is skipped before this is called.  A few event cards
    show a second free/wild cell; it must count as marked or every bingo answer
    after it is wrong.  The check is deliberately visual, not "OCR failed",
    so a genuinely unreadable number cannot silently become a free cell.
    """
    hsv = cv2.cvtColor(cell, cv2.COLOR_BGR2HSV)
    h, s, v = hsv[:, :, 0], hsv[:, :, 1], hsv[:, :, 2]
    orange = ((h >= 5) & (h <= 32) & (s >= 90) & (v >= 90))
    return float(orange.mean()) >= min_ratio


def read_card_ex(engine, warped, size: int = CANON, daubed=None, old=None):
    """Read the 5x5 card and also report visually confirmed free/wild cells.

    ``daubed`` plus ``old`` carry a locked round's knowledge: a pixel-confirmed
    daubed number cell reuses its locked value instead of being redecided, so
    an orange daub fill can never mark it as a free/wild cell.
    """
    values = [[None] * 5 for _ in range(5)]
    scores = [[0.0] * 5 for _ in range(5)]
    free = [[False] * 5 for _ in range(5)]
    free[2][2] = True  # standard 75-ball FREE space
    for r in range(5):
        for c in range(5):
            if r == 2 and c == 2:
                continue
            daub_guard = bool(daubed) and (r, c) in daubed
            if (daub_guard and old is not None
                    and old[r][c] is not None):
                values[r][c] = old[r][c]
                scores[r][c] = 0.90
                continue
            cell = cell_img(warped, r, c, size=size)
            txt, sc = ocr(engine, cell)
            # RapidOCR sometimes returns unicode digits such as '²' or '⑤'
            # that pass isdigit() but crash int(); use ASCII digits only.
            digits = "".join(ch for ch in txt
                             if ch.isascii() and ch.isdecimal())
            v = 0
            lo, hi = COL_RANGES[c]
            if 1 <= len(digits) <= 2:
                num = int(digits)
                if 1 <= num <= 75 and lo <= num <= hi:
                    v = num
            if v:
                values[r][c] = v
                scores[r][c] = sc
                continue
            if daub_guard:
                # Daubed but never readable: unknown, never free.
                continue
            if is_free_cell(cell):
                free[r][c] = True
    return values, scores, free


def read_card(engine, warped, size: int = CANON):
    """Backward-compatible two-value card reader."""
    values, scores, _ = read_card_ex(engine, warped, size=size)
    return values, scores


def detect_daubed_cells(warped, free, size: int = CANON,
                        strong: float = 0.55,
                        weak: float = 0.30):
    """Read already-daubed cells when the helper starts mid-round.

    A daub fills the cell with saturated colour; normal number cells are white
    with dark text, so the fraction is near 0 or near 1.  ``weak`` marks the
    ambiguous middle range, which makes Pick Me Up choose manually instead of
    guessing.
    """
    daubed, ambiguous = set(), False
    for r in range(5):
        for c in range(5):
            if free[r][c]:
                continue
            cell = cell_img(warped, r, c, size=size)
            ratio = cell_saturation(cell)
            if ratio >= strong:
                daubed.add((r, c))
            elif ratio >= weak:
                ambiguous = True
    return daubed, ambiguous


def cell_saturation(cell) -> float:
    """Fraction of a cell that is strongly saturated (the daub fill)."""
    if cell is None or cell.size == 0:
        return 0.0
    hsv = cv2.cvtColor(cell, cv2.COLOR_BGR2HSV)
    sat = ((hsv[:, :, 1] > 90) & (hsv[:, :, 2] > 70))
    return float(sat.mean())


# ---------------------------------------------------------------- ball

def _ball_candidates_color(img, grid: Grid):
    """Old saturation-blob fallback, used only if Hough finds nothing."""
    h, w = img.shape[:2]
    x0 = max(0, grid.x0 - 30)
    x1 = min(w, grid.x1 + 30)
    y0 = max(0, grid.y0 - int(grid.width * 0.85))
    y1 = max(1, grid.y0)
    roi = img[y0:y1, x0:x1]
    hsv = cv2.cvtColor(roi, cv2.COLOR_BGR2HSV)
    mask = (((hsv[:, :, 1] > 90) & (hsv[:, :, 2] > 70)) * 255).astype(np.uint8)
    mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, np.ones((3, 3), np.uint8))
    n, _, st, _ = cv2.connectedComponentsWithStats(mask, 8)
    cell = grid.width / 5.0
    out = []
    for i in range(1, n):
        bx, by, bw, bh, area = st[i]
        if area < 200:
            continue
        if not (0.75 <= bw / max(bh, 1) <= 1.35):
            continue
        if bw < 0.35 * cell or bh < 0.35 * cell:
            continue
        out.append((int(bx + x0), int(by + y0), int(bw), int(bh), int(area)))
    return out


def _ball_candidates(img, grid: Grid):
    """Plausible current/history balls above the grid.

    Hough circles keep working when Instant Ball draws a coloured banner
    behind the balls; a saturation blob would merge all three into one wide
    shape.  The old colour method is retained as a fallback.
    """
    h, w = img.shape[:2]
    x0 = max(0, grid.x0 - 30)
    x1 = min(w, grid.x1 + 30)
    y0 = max(0, grid.y0 - int(grid.width * 0.85))
    y1 = max(1, grid.y0)
    if x1 - x0 < 40 or y1 - y0 < 40:
        return []
    roi = img[y0:y1, x0:x1]
    gray = cv2.medianBlur(cv2.cvtColor(roi, cv2.COLOR_BGR2GRAY), 5)
    rh, rw = roi.shape[:2]
    cell = grid.width / 5.0
    min_r = max(10, int(0.20 * cell))
    max_r = max(min_r + 4, int(0.80 * cell))
    circles = cv2.HoughCircles(
        gray, cv2.HOUGH_GRADIENT, dp=1.2,
        minDist=max(10, int(0.35 * cell)),
        param1=100, param2=30,
        minRadius=min_r, maxRadius=max_r)
    out = []
    if circles is not None:
        hsv = cv2.cvtColor(roi, cv2.COLOR_BGR2HSV)
        sat = ((hsv[:, :, 1] > 90) & (hsv[:, :, 2] > 70))
        for cx, cy, r in circles[0]:
            cx, cy, r = int(cx), int(cy), int(r)
            if cx - r < 0 or cy - r < 0 or cx + r > rw or cy + r > rh:
                continue
            yy, xx = np.ogrid[:rh, :rw]
            disk = (xx - cx) ** 2 + (yy - cy) ** 2 <= (0.80 * r) ** 2
            if not disk.any() or float(sat[disk].mean()) < 0.10:
                continue
            out.append((int(cx - r + x0), int(cy - r + y0),
                        int(2 * r), int(2 * r), int(3.14 * r * r)))
    if out:
        return out
    return _ball_candidates_color(img, grid)


# ---- v1.13 ball selection (validated, see tools/test_ballpick.py) ----------
# v1.12 resolved the current ball as "largest candidate wins".  On the
# labelled screenshot corpus that picked the seated-cat blob instead of the
# real ball in 18/28 files: Hough draws a big pale circle around the cat and
# the history balls sit at its bottom edge, so that blob out-sizes the real
# ball and its OCR crop reads a fragment with 0.98-1.00 confidence.
# Measured replacement (28 files, value confirmed by production OCR):
#   OLD largest            10/28
#   hot-zone + size band   28/28 (at every zone width 0.50-0.65)
# Ring/face identity probes were tested and REJECTED: Hough box offsets
# collapse the ring measurement on real balls (0.006-0.01 on the 42px
# history balls) while cat phantoms still measure up to 0.42 - the
# distributions overlap, so identity probes only break real balls.
BALL_ZONE_FRAC = 0.60   # live ball sits right of this fraction of width
BALL_DMIN_F = 0.80      # drawn-ball diameter band, relative to grid cell
BALL_DMAX_F = 1.35

def _ball_zone_pool(cands, grid: Grid):
    """Candidates that can be the live ball: size band first, then hot zone.

    Falls back to the whole size band, then to every candidate, so a solo
    ball that parks mid-window (round transitions) is still found.
    """
    cell = (grid.x1 - grid.x0) / 5.0
    zx = grid.x0 + BALL_ZONE_FRAC * (grid.x1 - grid.x0)
    banded = [c for c in cands
              if BALL_DMIN_F * cell <= (c[2] + c[3]) / 2 <= BALL_DMAX_F * cell]
    hot = [c for c in banded if c[0] + c[2] / 2 >= zx]
    if hot:
        return hot
    return banded or list(cands)

def select_current(cands, grid: Grid):
    """The live ball box from any candidate list (v1.13 validated rule).

    Rightmost member of the size band inside the hot zone: the game always
    enters the new ball at the right of the strip, the cat blob never gets
    there, and the drawn ball size is bounded by the card cell.
    """
    if not cands:
        return None
    return max(_ball_zone_pool(cands, grid), key=lambda c: c[0])[:4]

def find_ball(img, grid: Grid):
    """Current ball: hot-zone + size-band member, rightmost (v1.13)."""
    cands = _ball_candidates(img, grid)
    return select_current(cands, grid)

def _ball_clusters(cands, cell):
    """Merge boxes that cover the same drawn ball, keep readable ones.

    Hough draws offset/oversized duplicates of one ball; merging by centre
    proximity and keeping the box closest to the typical band size retains
    the box whose crop is centred on the digits.
    """
    keep = [c for c in cands
            if BALL_DMIN_F * cell <= (c[2] + c[3]) / 2 <= BALL_DMAX_F * cell]
    if not keep:
        keep = list(cands)
    ds = sorted((c[2] + c[3]) / 2 for c in keep)
    typical = ds[len(ds) // 2]
    reps = []
    for c in sorted(keep, key=lambda c: -c[2] * c[3]):
        for i, r in enumerate(reps):
            cx, cy = c[0] + c[2] / 2, c[1] + c[3] / 2
            rx, ry = r[0] + r[2] / 2, r[1] + r[3] / 2
            dist = ((cx - rx) ** 2 + (cy - ry) ** 2) ** 0.5
            if dist <= 0.75 * max((c[2] + c[3]) / 2, (r[2] + r[3]) / 2):
                if abs((c[2] + c[3]) / 2 - typical) < \
                        abs((r[2] + r[3]) / 2 - typical):
                    reps[i] = c
                break
        else:
            reps.append(c)
    return reps

def find_balls(img, grid: Grid, max_balls: int = 3):
    """The large live balls above the grid (Instant Ball shows 2-3).

    v1.12 kept every blob within 68% of the largest radius, so one
    over-sized cat blob inflated the ceiling and pushed the real balls out.
    Selection now uses the size band plus centre-proximity clustering:
    duplicate circles collapse to one ball, cat blobs never enter the
    radius ranking, and history balls stay below the band.
    """
    cands = _ball_candidates(img, grid)
    if not cands:
        return []
    reps = _ball_clusters(cands, (grid.x1 - grid.x0) / 5.0)
    reps.sort(key=lambda c: -c[2] * c[3])
    reps = reps[:max_balls]
    reps.sort(key=lambda c: c[0])
    return [c[:4] for c in reps]



def _row_bands(th):
    """Split glyph ink into bands at the widest blank horizontal gap.

    Works at any ball size, unlike a fixed pixel gap.
    """
    rows = (th > 0).sum(axis=1)
    zero = rows == 0
    runs, inrun, start = [], False, 0
    for y, z in enumerate(zero):
        if z and not inrun:
            start, inrun = y, True
        elif not z and inrun:
            runs.append((start, y))
            inrun = False
    if inrun:
        runs.append((start, len(zero)))

    interior = [r for r in runs if r[0] > 1 and r[1] < len(zero) - 1]
    if not interior:
        return [(0, len(zero))]
    gap = max(interior, key=lambda r: r[1] - r[0])
    if gap[1] - gap[0] < 2:
        return [(0, len(zero))]
    bands = []
    if gap[0] > 0:
        bands.append((0, gap[0]))
    if gap[1] < len(zero):
        bands.append((gap[1], len(zero)))
    return bands or [(0, len(zero))]


def read_ball(engine, img, bbox, fast: bool = False):
    """Read a ball.  fast=True skips the letter OCR.

    The letter is fully determined by the number's B/I/N/G/O range, so the
    letter read is redundant.  Skipping it saves ~50 ms per ball, which matters
    when Instant Ball shows three balls at once.
    """
    x, y, w, h = bbox
    cx, cy = x + w // 2, y + h // 2
    r = min(w, h) // 2
    inset = max(2, int(r * 0.22))
    H, W = img.shape[:2]
    ix0, ix1 = max(0, cx - r + inset), min(W, cx + r - inset)
    iy0, iy1 = max(0, cy - r + inset), min(H, cy + r - inset)
    inner = img[iy0:iy1, ix0:ix1]

    g = cv2.cvtColor(inner, cv2.COLOR_BGR2GRAY)
    _, th = cv2.threshold(g, 130, 255, cv2.THRESH_BINARY_INV)
    hh, ww = th.shape
    m = max(1, int(0.06 * min(hh, ww)))
    th[:m, :] = 0
    th[-m:, :] = 0
    th[:, :m] = 0
    th[:, -m:] = 0

    bands = _row_bands(th)
    two_bands = len(bands) >= 2
    if fast and two_bands:
        bands = bands[1:]  # bottom band is the number
    letter, letter_sc = "", 0.0
    number, number_sc = None, 0.0

    for idx, (by0, by1) in enumerate(bands):
        band = th[by0:by1]
        ys, xs = np.where(band > 0)
        if len(xs) < 25:
            continue
        ux0, ux1 = int(xs.min()), int(xs.max()) + 1
        uy0, uy1 = int(ys.min()) + by0, int(ys.max()) + 1 + by0
        pad = max(2, int(0.12 * (uy1 - uy0)))
        crop = inner[max(0, uy0 - pad):min(hh, uy1 + pad),
                     max(0, ux0 - pad):min(ww, ux1 + pad)]
        if crop.size == 0:
            continue
        txt, sc = ocr(engine, crop, scale=6.0)
        t = txt.strip()

        is_top = (not fast) and idx == 0 and two_bands
        if is_top:
            if t.upper() in LETTERS and len(t) == 1:
                letter, letter_sc = t.upper(), sc
            continue

        digits = "".join(ch for ch in t
                         if ch.isascii() and ch.isdecimal())
        if 1 <= len(digits) <= 2:
            number, number_sc = int(digits), sc
        elif not two_bands and t.upper() in LETTERS and len(t) == 1:
            # single band that turned out to be a letter, not a number
            letter, letter_sc = t.upper(), sc

    return {"letter": letter, "letter_score": letter_sc,
            "number": number, "number_score": number_sc}


def read_ball_robust(engine, img, bbox, min_score: float = 0.90,
                     fast: bool = True):
    """Read a ball whose Hough box may be slightly too large.

    Instant Ball can draw a coloured banner that makes Hough pick a wider
    circle.  Try a few centred shrink factors; only accept a non-default
    scale when at least two scales agree on the same number.  A missed read is
    better than reading half a number as a different ball.
    """
    x, y, w, h = bbox
    cx, cy = x + w // 2, y + h // 2
    votes: dict[int, tuple[int, float]] = {}
    for f in (1.0, 0.92, 0.85, 0.78, 0.70):
        nw, nh = max(4, int(w * f)), max(4, int(h * f))
        bb = (cx - nw // 2, cy - nh // 2, nw, nh)
        ball = read_ball(engine, img, bb, fast=fast)
        num = ball["number"]
        if num is None or not (1 <= num <= 75) or ball["number_score"] < min_score:
            continue
        count, best = votes.get(num, (0, 0.0))
        votes[num] = (count + 1, max(best, ball["number_score"]))
        if f == 1.0:
            return ball  # unshrunk read is the safest
    if not votes:
        return {"letter": "", "letter_score": 0.0,
                "number": None, "number_score": 0.0}
    num, (count, best) = max(votes.items(), key=lambda kv: (kv[1][0], kv[1][1]))
    if count < 2:
        return {"letter": "", "letter_score": 0.0,
                "number": None, "number_score": 0.0}
    return {"letter": "", "letter_score": 0.0,
            "number": num, "number_score": best}


# ---------------------------------------------------------------- power-up popups

def find_pink_band(img):
    """Locate the wide pink/red title strip used by Pick Me Up."""
    h, w = img.shape[:2]
    hsv = cv2.cvtColor(img, cv2.COLOR_BGR2HSV)
    hue, sat, val = hsv[:, :, 0], hsv[:, :, 1], hsv[:, :, 2]
    # Coral/pink sits at the red end of the hue wheel in OpenCV (0-179).
    mask = (((hue >= 155) | (hue <= 8)) & (sat >= 70) & (val >= 100))
    mask = (mask * 255).astype(np.uint8)
    mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, np.ones((5, 5), np.uint8))
    n, _, st, _ = cv2.connectedComponentsWithStats(mask, 8)
    best = None
    for i in range(1, n):
        x, y, bw, bh, area = st[i]
        if bw < 0.12 * w or bh < 14 or bh > 0.25 * h:
            continue
        if bw / max(bh, 1) < 2.5:
            continue
        if area < 0.004 * w * h:
            continue
        score = (bw, area)
        if best is None or score > best[0]:
            best = (score, int(x), int(y), int(bw), int(bh))
    return best[1:] if best else None


def detect_instant_ball(img, grid: Grid, bboxes=None) -> bool:
    """True when the game is showing three real Instant Balls.

    Normal play also shows small call-history balls above the card, and a
    single current ball.  The game's Instant Ball text/pill is not always
    visible in every capture, so this geometry check requires three large
    balls in the same row, which the normal history row does not look like.
    """
    if grid is None:
        return False
    if bboxes is None:
        bboxes = find_balls(img, grid, max_balls=3)
    if len(bboxes) < 3:
        return False

    cell = grid.width / 5.0
    large = []
    for x, y, w, h in bboxes:
        if w >= 0.78 * cell and h >= 0.78 * cell:
            large.append((x, y, w, h))
    if len(large) < 3:
        return False

    centres = [y + h / 2 for _x, y, _w, h in large]
    if max(centres) - min(centres) > 0.35 * cell:
        return False

    # They must sit above the playing grid, like the power-up screenshot.
    top = min(y for _x, y, _w, _h in large)
    return top < grid.y0


def find_popup_title(engine, img):
    """Return (title_bbox, text) when the screen is the Pick Me Up popup."""
    band = find_pink_band(img)
    if band is None:
        return None
    x, y, w, h = band
    H, W = img.shape[:2]
    pad = max(4, int(0.04 * h))
    crop = img[max(0, y - pad):min(H, y + h + pad),
               max(0, x - pad):min(W, x + w + pad)]
    if crop.size == 0:
        return None
    txt, _ = ocr(engine, crop, scale=2.0)
    norm = "".join(ch for ch in txt.upper() if ch.isalnum())
    if "PICKMEUP" in norm or ("PICK" in norm and "UP" in norm):
        return band, txt.strip()
    return None


def find_instant_ball_banner(img, grid: Grid):
    """Detect the pink INSTANT BALL pill under the card.

    Normal play also shows small call-history balls above the card, so multiple
    ball boxes alone must never become Instant Ball guidance.  The pill is a
    small, wide, centred marker directly below the card.  The normal BINGO
    button is farther sideways and not pill-shaped, so it does not match.
    """
    if grid is None:
        return None
    H, W = img.shape[:2]
    x0 = max(0, grid.x0 - int(0.25 * grid.width))
    x1 = min(W, grid.x1 + int(0.25 * grid.width))
    y0 = max(0, grid.y1 - int(0.10 * grid.height))
    y1 = min(H, grid.y1 + int(0.35 * grid.height))
    if x1 - x0 < 40 or y1 - y0 < 20:
        return None

    roi = img[y0:y1, x0:x1]
    hsv = cv2.cvtColor(roi, cv2.COLOR_BGR2HSV)
    hue, sat, val = hsv[:, :, 0], hsv[:, :, 1], hsv[:, :, 2]
    # Coral/pink/red sits at the red end of the hue wheel in OpenCV (0-179).
    mask = ((((hue >= 130) | (hue <= 12)) & (sat >= 70) & (val >= 100))
            * 255).astype(np.uint8)
    mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, np.ones((3, 3), np.uint8))

    n, _, st, _ = cv2.connectedComponentsWithStats(mask, 8)
    card_bottom = grid.y1
    card_cx = (grid.x0 + grid.x1) / 2
    best = None
    for i in range(1, n):
        bx, by, bw, bh, area = st[i]
        bw, bh = int(bw), int(bh)
        if bw < 0.35 * grid.width or bw > 0.95 * grid.width:
            continue
        if bh < 0.22 * grid.height or bh > 0.35 * grid.height:
            continue
        aspect = bw / max(bh, 1)
        if aspect < 1.3 or aspect > 3.8:
            continue
        if area < 0.035 * grid.width * grid.height:
            continue
        ax, ay = int(bx + x0), int(by + y0)
        cx, cy = ax + bw / 2, ay + bh / 2
        # The real banner is a speech bubble attached to the power-up button,
        # so its top sits at the card's bottom edge.  The permanent power-up
        # tray below the card starts lower and is not a call.
        if ay < card_bottom - 0.12 * grid.height:
            continue
        if ay > card_bottom + 0.06 * grid.height:
            continue
        if cy > card_bottom + 0.30 * grid.height:
            continue
        if abs(cx - card_cx) > 0.40 * grid.width:
            continue
        score = (area, -abs(cy - card_bottom))
        if best is None or score > best[0]:
            best = (score, (ax, ay, bw, bh))

    return best[1] if best else None


def find_popup_balls(img, region, max_balls: int = 4):
    """Find the large circular offered balls inside a Pick Me Up popup.

    Hough circles work here where colour contours do not: the coloured coffee
    cup touches two of the balls, so a saturation blob merges them into one
    shape.  The balls are clean circles with a white rim, and Hough finds all
    four at once.  If it cannot, the caller shows "choose manually" rather
    than guessing.
    """
    H, W = img.shape[:2]
    x0, y0, x1, y1 = (int(v) for v in region)
    x0, y0 = max(0, x0), max(0, y0)
    x1, y1 = min(W, x1), min(H, y1)
    if x1 - x0 < 60 or y1 - y0 < 60:
        return []
    roi = img[y0:y1, x0:x1]
    gray = cv2.medianBlur(cv2.cvtColor(roi, cv2.COLOR_BGR2GRAY), 5)
    rh, rw = roi.shape[:2]
    min_r = max(12, int(0.035 * rw))
    max_r = max(min_r + 4, int(0.09 * rw))
    circles = cv2.HoughCircles(
        gray, cv2.HOUGH_GRADIENT, dp=1.2,
        minDist=max(20, int(0.10 * rw)),
        param1=100, param2=30,
        minRadius=min_r, maxRadius=max_r)
    if circles is None:
        return []

    hsv = cv2.cvtColor(roi, cv2.COLOR_BGR2HSV)
    sat = ((hsv[:, :, 1] > 90) & (hsv[:, :, 2] > 70))
    out = []
    for cx, cy, r in circles[0]:
        cx, cy, r = int(cx), int(cy), int(r)
        if cx - r < 0 or cy - r < 0 or cx + r > rw or cy + r > rh:
            continue
        yy, xx = np.ogrid[:rh, :rw]
        disk = (xx - cx) ** 2 + (yy - cy) ** 2 <= (0.82 * r) ** 2
        color_ratio = float(sat[disk].mean()) if disk.any() else 0.0
        if color_ratio < 0.12:
            continue
        out.append((int(cx - r + x0), int(cy - r + y0), int(2 * r), int(2 * r),
                    color_ratio))
    if not out:
        return []
    out.sort(key=lambda b: b[4], reverse=True)
    out = out[:max_balls]
    out.sort(key=lambda b: (b[1], b[0]))
    return [b[:4] for b in out]


# ---------------------------------------------------------------- rules

def letter_for(n: int) -> Optional[str]:
    for i, (lo, hi) in enumerate(COL_RANGES):
        if lo <= n <= hi:
            return LETTERS[i]
    return None


def validate_card(values, free=None):
    if free is None:
        free = [[False] * 5 for _ in range(5)]
        free[2][2] = True
    issues, seen = [], {}
    readable = 0
    for r in range(5):
        for c in range(5):
            if free[r][c]:
                continue
            v = values[r][c]
            if v is None:
                issues.append(f"cell {r},{c}: unreadable")
                continue
            readable += 1
            lo, hi = COL_RANGES[c]
            if not (lo <= v <= hi):
                issues.append(f"cell {r},{c}: {v} outside column {LETTERS[c]} range {lo}-{hi}")
            if v in seen:
                issues.append(f"duplicate {v} at {seen[v]} and {(r, c)}")
            seen[v] = (r, c)
    # A real bingo card has almost all cells readable.  A new-round frame can
    # make the still-hidden cells look like pumpkin/free, so it must be rejected
    # until the card is dealt.
    if readable < 18:
        issues.append(f"too few readable numbers ({readable})")
    return issues


def locate_number(values, n: int):
    for r in range(5):
        for c in range(5):
            if values[r][c] == n:
                return r, c
    return None


# ---------------------------------------------------------------- annotation

def annotate(img, grid: Grid, values, ball_bbox, ball, decision: str):
    vis = img.copy()
    cv2.rectangle(vis, (grid.x0, grid.y0), (grid.x1, grid.y1), (0, 220, 0), 2)
    for x in grid.xs:
        cv2.line(vis, (x, grid.y0), (x, grid.y1), (0, 200, 0), 1)
    for y in grid.ys:
        cv2.line(vis, (grid.x0, y), (grid.x1, y), (0, 200, 0), 1)

    cw = grid.width / 5.0
    ch = grid.height / 5.0
    for r in range(5):
        for c in range(5):
            v = values[r][c]
            if v is None:
                continue
            px = int(grid.x0 + c * cw + 4)
            py = int(grid.y0 + r * ch + 18)
            cv2.putText(vis, str(v), (px, py), cv2.FONT_HERSHEY_SIMPLEX,
                        0.5, (0, 140, 0), 1, cv2.LINE_AA)

    if ball_bbox:
        x, y, w, h = ball_bbox
        cv2.rectangle(vis, (x, y), (x + w, y + h), (255, 0, 255), 2)
        cv2.putText(vis, f"{ball['letter']} {ball['number']}", (x - 10, y - 10),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.7, (255, 0, 255), 2, cv2.LINE_AA)

    cv2.putText(vis, decision, (20, 40), cv2.FONT_HERSHEY_SIMPLEX,
                0.9, (0, 0, 255), 2, cv2.LINE_AA)
    return vis
