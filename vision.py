"""Bingo vision core: grid locate -> rectify -> read card -> read ball -> decide.

Everything here is pure computer vision. No capture, no overlay, no game access.
"""
from __future__ import annotations

import cv2
import numpy as np
from dataclasses import dataclass
from typing import Optional

COL_RANGES = ((1, 15), (16, 30), (31, 45), (46, 60), (61, 75))
LETTERS = ("B", "I", "N", "G", "O")
CANON = 500  # rectified grid is CANON x CANON


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
        if best is None or (x1 - x0) > best[0]:
            best = (x1 - x0, x0, min(c[1] for c in g), x1,
                    max(c[1] + c[3] for c in g))
    if best is None:
        return None
    return best[1], best[2], best[3], best[4]


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
    if ys is None:
        return None

    # --- vertical boundaries
    gy0, gy1 = ys[0], ys[-1]
    gh = max(1, gy1 - gy0)
    kv = max(40, int(0.14 * gh))
    vm = cv2.morphologyEx(bw, cv2.MORPH_OPEN,
                          cv2.getStructuringElement(cv2.MORPH_RECT, (1, kv)))
    x_lo, x_hi = max(0, hx0 - 20), min(W, hx1 + 20)
    cols = [x for x in range(x_lo, x_hi)
            if int((vm[gy0:gy1, x] > 0).sum()) > 0.45 * gh]
    xs = _pick_even(_collapse(cols), 6)
    if xs is None:
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


def read_card(engine, warped, size: int = CANON):
    values = [[None] * 5 for _ in range(5)]
    scores = [[0.0] * 5 for _ in range(5)]
    for r in range(5):
        for c in range(5):
            if r == 2 and c == 2:
                continue  # standard 75-ball FREE space
            txt, sc = ocr(engine, cell_img(warped, r, c, size=size))
            digits = "".join(ch for ch in txt if ch.isdigit())
            if 1 <= len(digits) <= 2:
                v = int(digits)
                if 1 <= v <= 75:
                    values[r][c] = v
                    scores[r][c] = sc
    return values, scores


# ---------------------------------------------------------------- ball

def find_ball(img, grid: Grid):
    """Largest saturated, roughly-circular blob above the grid = current ball.

    Colour-agnostic on purpose: B/I/N/G/O balls use five different hues, and
    the call history shows smaller balls of the same hues.
    """
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
    best = None
    for i in range(1, n):
        bx, by, bw, bh, area = st[i]
        if area < 200:
            continue
        if not (0.75 <= bw / max(bh, 1) <= 1.35):
            continue
        if bw < 0.35 * cell or bh < 0.35 * cell:
            continue
        # Largest wins; on a tie the right-most wins (current ball sits right
        # of the call history, which can use the same ball size).
        score = (bw * bh, bx)
        if best is None or score > best[0]:
            best = (score, int(bx + x0), int(by + y0), int(bw), int(bh))
    return best[1:] if best else None


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


def read_ball(engine, img, bbox):
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

        is_top = idx == 0 and len(bands) >= 2
        if is_top:
            if t.upper() in LETTERS and len(t) == 1:
                letter, letter_sc = t.upper(), sc
            continue

        digits = "".join(ch for ch in t if ch.isdigit())
        if 1 <= len(digits) <= 2:
            number, number_sc = int(digits), sc
        elif len(bands) == 1 and t.upper() in LETTERS and len(t) == 1:
            # single band that turned out to be a letter, not a number
            letter, letter_sc = t.upper(), sc

    return {"letter": letter, "letter_score": letter_sc,
            "number": number, "number_score": number_sc}


# ---------------------------------------------------------------- rules

def letter_for(n: int) -> Optional[str]:
    for i, (lo, hi) in enumerate(COL_RANGES):
        if lo <= n <= hi:
            return LETTERS[i]
    return None


def validate_card(values):
    issues, seen = [], {}
    for r in range(5):
        for c in range(5):
            if r == 2 and c == 2:
                continue
            v = values[r][c]
            if v is None:
                issues.append(f"cell {r},{c}: unreadable")
                continue
            lo, hi = COL_RANGES[c]
            if not (lo <= v <= hi):
                issues.append(f"cell {r},{c}: {v} outside column {LETTERS[c]} range {lo}-{hi}")
            if v in seen:
                issues.append(f"duplicate {v} at {seen[v]} and {(r, c)}")
            seen[v] = (r, c)
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
