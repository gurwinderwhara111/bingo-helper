"""tools/simulate_numbers.py — all-75 numbers verification harness (v1.11 audit).

Four independent checks:

  cards  Every bingo value 1..75 rendered as the game renders it, at every
         grid width seen live (410 / 368 / 332 good, 246 / 234 bad), clean
         AND fully daubed, through the production template reader
         (read_card_fast) and the OCR fallback (read_card_ex).
  balls  Every ball 1..75 rendered in the game's ball style at several
         sizes, through read_ball and read_ball_robust with the live
         thresholds.  Runs both plausible ball styles so the video pass
         can say which one the real game uses.
  video  Real ball crops harvested from the recorded gameplay, clustered
         by appearance, read by the production readers and exported as a
         contact sheet for manual verification.  Also cross-checks the
         production V.find_balls detector against a naive colour-circle
         detector to expose production misses.

Usage:
  python tools/simulate_numbers.py cards
  python tools/simulate_numbers.py balls
  python tools/simulate_numbers.py video
"""
from __future__ import annotations

import csv
import sys
import time
from pathlib import Path

import cv2
import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import vision as V  # noqa: E402

OUT = ROOT / "debug" / "sim"
OUT.mkdir(parents=True, exist_ok=True)

# ---- measured game colours (RGB) from the fact-check notes -----------------
INK = (98, 90, 83)          # #625A53 card digit ink
INK_ON_DAUB = (120, 68, 50)  # digits visible through salmon daub (approx)
DAUB = (237, 156, 125)      # #ED9C7D salmon daub fill (approx)
GRID_LINE = (219, 225, 249)  # #F9E1DB internal grid lines (BGR-ish below)
BALL_COLORS = {  # measured header hexes; ball faces use the same scheme
    "B": (41, 156, 210),
    "I": (244, 99, 59),
    "N": (179, 112, 226),
    "G": (23, 167, 108),
    "O": (242, 151, 14),
}
DARK_INK_BALL = (26, 45, 69)   # dark navy digits on a white ball face

FONT_CANDIDATES = ["ARLRDBD.TTF", "arlrdbd.ttf", "seguisb.ttf", "segoeuib.ttf",
                   "arialbd.ttf"]
FONT_DIR = Path(r"C:\Windows\Fonts")

VIDEO_DIR = ROOT / "gameplay videos"
VIDEOS = ["Bingo normal gameplay 2.mp4", "bingo normal live play1.mp4"]
VOL1 = (776, 412, 1144, 758)    # observed live lock in the videos (x0,y0,x1,y1)

COLS = list(V.COL_RANGES)


def load_font(size: int):
    from PIL import ImageFont
    for name in FONT_CANDIDATES:
        try:
            return ImageFont.truetype(str(FONT_DIR / name), size)
        except OSError:
            continue
    raise RuntimeError("no system font found - checked " + ", ".join(FONT_CANDIDATES))


# ---------------------------------------------------------------------------
# card rendering
# ---------------------------------------------------------------------------

def render_card_frame(width: int, values: list[list[int]], daubs: bool) -> np.ndarray:
    """Synthetic card in the game's own style at a native grid width."""
    from PIL import Image, ImageDraw, ImageFont
    cell = width // 5
    x0, y0 = 60, 40
    w = x0 * 2 + cell * 5
    h = y0 * 2 + cell * 5
    img = Image.new("RGB", (w, h), (255, 255, 255))
    d = ImageDraw.Draw(img)

    # internal grid lines (soft pink like the game)
    line = (0xF9, 0xE1, 0xDB)
    for i in range(6):
        d.line([(x0 + i * cell, y0), (x0 + i * cell, y0 + cell * 5)],
               fill=line, width=1)
        d.line([(x0, y0 + i * cell), (x0 + cell * 5, y0 + i * cell)],
               fill=line, width=1)

    cap = 0.52 * cell                      # digit cap-height target
    em = int(round(cap / 0.72))            # arial-rounded cap ≈ 0.72 em
    font = load_font(em)
    for r in range(5):
        for c in range(5):
            v = values[r][c]
            if v == 0:
                continue
            cx = x0 + c * cell + cell / 2
            cy = y0 + r * cell + cell / 2
            bbox = font.getbbox(str(v))
            tw, th = bbox[2] - bbox[0], bbox[3] - bbox[1]
            if daubs:
                d.rectangle([x0 + c * cell + 3, y0 + r * cell + 3,
                             x0 + (c + 1) * cell - 4, y0 + (r + 1) * cell - 4],
                            fill=DAUB)
                color = INK_ON_DAUB
            else:
                color = INK
            d.text((cx - tw / 2 - bbox[0], cy - th / 2 - bbox[1]),
                   str(v), font=font, fill=color)
    frame = cv2.cvtColor(np.asarray(img), cv2.COLOR_RGB2BGR)
    gx0, gy0, gx1, gy1 = x0, y0, x0 + cell * 5, y0 + cell * 5
    xs = [gx0 + i * cell for i in range(6)]
    ys = [gy0 + i * cell for i in range(6)]
    grid = V.Grid(x0=gx0, y0=gy0, x1=gx1, y1=gy1, xs=xs, ys=ys)
    extra = np.zeros((1, 4), np.float32)[0].tolist()
    frame[...][:2] = frame[...][:2]  # no-op, keep array writable
    return frame, grid


def truth_cards():
    """15 cards; together they carry every value 1..75 exactly once.

    (2, 2) stays 0: production hardcodes it as the standard FREE space, so
    it is neither render-able truth nor a reader failure.
    """
    cards = []
    for k in range(3):
        card = [[15 * c + k * 5 + r + 1 for c in range(5)] for r in range(5)]
        card[2][2] = 0
        cards.append(card)
    # 4th card recovers the three values the free space overwrote (33/38/43)
    extra = [[0] * 5 for _ in range(5)]
    extra[0][2], extra[1][2], extra[3][2] = 33, 38, 43
    cards.append(extra)
    return cards


def run_cards():
    engine = None
    rows = []
    for width in (410, 368, 332, 246, 234):
        for ki, card in enumerate(truth_cards()):
            for mode, flag in (("clean", False), ("daub", True)):
                frame, grid = render_card_frame(width, card, flag)
                warped = V.rectify(frame, grid)
                fast = V.read_card_fast(warped)
                if fast is None:
                    for c in range(5):
                        for r in range(5):
                            rows.append([ki, width, mode, r, c, card[r][c],
                                         "fast-none", 0.0])
                    continue
                values, scores, free = fast
                for r in range(5):
                    for c in range(5):
                        truth = card[r][c]
                        got = values[r][c]
                        ok = (got == truth)
                        rows.append([ki, width, mode, r, c, truth,
                                     (str(got) if ok else f"fast:{got}"),
                                     round(scores[r][c], 3)])
                # OCR fallback at the two extremes only (runtime)
                if width in (410, 246):
                    if engine is None:
                        from rapidocr import RapidOCR
                        engine = RapidOCR()
                    ex = V.read_card_ex(engine, warped)
                    if ex is None:
                        continue
                    evalues, escores, efree = ex
                    for r in range(5):
                        for c in range(5):
                            truth = card[r][c]
                            if truth == 0:
                                continue
                            got = evalues[r][c]
                            if got != truth:
                                rows.append([ki, width, mode, r, c, truth,
                                             f"ex:{got}",
                                             round(escores[r][c], 3)])

    with open(OUT / "cards_results.csv", "w", newline="", encoding="utf8") as f:
        w = csv.writer(f)
        w.writerow(["card", "width", "mode", "row", "col", "truth", "got",
                    "score"])
        w.writerows(rows)

    fails = [r for r in rows if r[5] != 0 and r[6] != str(r[5])]
    tpl_none = [r for r in fails if r[6].endswith(":None")]
    tpl_wrong = [r for r in fails if not r[6].endswith(":None")]
    exfail = [r for r in rows if r[5] != 0 and r[6].startswith("ex:")
              and r[6] != str(r[5])]
    print(f"cards: {len(rows)} cell checks, "
          f"{len(tpl_none)} template-unreadable, "
          f"{len(tpl_wrong)} template-wrong-number, "
          f"{len(exfail)} OCR-fallback wrong")
    for width, mode in sorted({(r[1], r[2]) for r in rows}):
        total = sum(1 for r in rows if r[1] == width and r[2] == mode and r[5] != 0)
        badn = sum(1 for r in fails if r[1] == width and r[2] == mode)
        print(f"  {width}px {mode:5s}: {total - badn}/{total} ok")
    if fails:
        print("  failing reads (card,width,mode,cell,truth->got,score):")
        for r in fails[:40]:
            print(f"    c{r[0]} {r[1]}px {r[2]:5s} r{r[3]}c{r[4]}: {r[5]} -> {r[6]} ({r[7]})")
        if len(fails) > 40:
            print(f"    ... and {len(fails) - 40} more (see {OUT / 'cards_results.csv'})")
    seen = {r[5] for r in rows if r[5] != 0}
    print(f"coverage: {len(seen)}/75 distinct values exercised")
    missing = sorted(set(range(1, 76)) - seen)
    if missing:
        print(f"  MISSING from coverage: {missing}")


# ---------------------------------------------------------------------------
# ball rendering  (two styles; the video pass tells which the game uses)
# ---------------------------------------------------------------------------

def render_ball(n: int, d: int, style: str) -> np.ndarray:
    """One ball: style 'white-ink' = coloured face + white digits,
    style 'dark-ink' = coloured ring + white face + dark navy digits."""
    from PIL import Image, ImageDraw, ImageFont
    letter = V.letter_for(n) or "?"
    pad = 8
    w = h = d + pad * 2
    img = Image.new("RGB", (w, h), (245, 242, 238))
    dr = ImageDraw.Draw(img)
    col = BALL_COLORS[letter]
    cx, cy, r = w / 2, h / 2, d / 2
    dr.ellipse([cx - r, cy - r, cx + r, cy + r], fill=col)
    if style == "dark-ink":
        rr = r * 0.70
        dr.ellipse([cx - rr, cy - rr, cx + rr, cy + rr], fill=(255, 255, 255))
        ink = DARK_INK_BALL
    else:
        ink = (255, 255, 255)
    f1 = load_font(int(d * 0.30))
    f2 = load_font(int(d * 0.40))
    b1 = f1.getbbox(letter)
    dr.text((cx - (b1[2] - b1[0]) / 2 - b1[0], cy - d * 0.36 - b1[1]),
            letter, font=f1, fill=ink)
    num = str(n)
    b2 = f2.getbbox(num)
    dr.text((cx - (b2[2] - b2[0]) / 2 - b2[0], cy + d * 0.05 - b2[1]),
            num, font=f2, fill=ink)
    frame = cv2.cvtColor(np.asarray(img), cv2.COLOR_RGB2BGR)
    return frame


def run_balls():
    from rapidocr import RapidOCR
    engine = RapidOCR()
    rows = []
    for style in ("white-ink", "dark-ink"):
        for d in (64, 56, 48, 44, 40):
            for n in range(1, 76):
                fr = render_ball(n, d, style)
                cx = fr.shape[1] // 2
                cy = fr.shape[0] // 2
                bbox = (cx - d // 2, cy - d // 2, d, d)
                b1 = V.read_ball(engine, fr, bbox)          # letter-aware path
                b2 = V.read_ball_robust(engine, fr, bbox, min_score=0.92)
                rows.append([style, d, n,
                             b1["number"], round(b1["number_score"], 3),
                             b2["number"], round(b2["number_score"], 3),
                             b1["letter"]])
    with open(OUT / "balls_results.csv", "w", newline="", encoding="utf8") as f:
        w = csv.writer(f)
        w.writerow(["style", "diam", "n", "read1", "sc1", "robust", "sc2",
                    "letter_ocr"])
        w.writerows(rows)
    print("balls (synthetic):")
    for style in ("white-ink", "dark-ink"):
        for d in (64, 56, 48, 44, 40):
            sub = [r for r in rows if r[0] == style and r[1] == d]
            ok = sum(1 for r in sub if r[5] == r[2])
            print(f"  {style:9s} {d}px: {ok}/75 robust-reads correct")
    misses = [(r[0], r[1], r[2], r[5]) for r in rows if r[5] != r[2]
              and r[0] == "dark-ink"]
    if misses:
        print("  dark-ink failures (style,d,n,robust):")
        for m in misses[:30]:
            print(f"    {m}")


# ---------------------------------------------------------------------------
# video pass: real crops, clustering, reading, contact sheet
# ---------------------------------------------------------------------------

def naive_balls(region: np.ndarray, dmin: int, dmax: int):
    hsv = cv2.cvtColor(region, cv2.COLOR_BGR2HSV)
    sat, val = hsv[:, :, 1], hsv[:, :, 2]
    mask = ((sat >= 90) & (val >= 120)).astype(np.uint8) * 255
    mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, np.ones((3, 3), np.uint8))
    n, _, st, _ = cv2.connectedComponentsWithStats(mask, 8)
    out = []
    H, W = region.shape[:2]
    for i in range(1, n):
        x, y, w, h, area = st[i]
        if not (dmin <= w <= dmax and dmin <= h <= dmax):
            continue
        if abs(w - h) > 0.25 * max(w, h):
            continue
        if area < 0.45 * w * h:
            continue          # filled disk, not a ring
        if w < 10 or h < 10:
            continue
        if x <= 2 or y <= 2 or x + w >= W - 2 or y + h >= H - 2:
            continue
        out.append((x, y, w, h))
    return out


def crop_of(frame, x, y, w, h, grow=2):
    x0, y0 = max(0, x - grow), max(0, y - grow)
    x1 = min(frame.shape[1], x + w + grow)
    y1 = min(frame.shape[0], y + h + grow)
    return frame[y0:y1, x0:x1].copy()


def sig_of(crop):
    g = cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY)
    g = cv2.resize(g, (24, 24), interpolation=cv2.INTER_AREA)
    return g.astype(np.int16)


def run_video():
    from rapidocr import RapidOCR
    engine = RapidOCR()
    clusters: list[dict] = []

    def add(crop, fid, dia):
        s = sig_of(crop)
        best, bd = None, 1e9
        for cl in clusters:
            bd_ = float(np.abs(s - cl["sig"]).sum())
            if bd_ < bd:
                best, bd = cl, bd_
        if best is not None and bd < 22 * 24:
            best["n"] += 1
            best["ids"].append(fid)
            if dia > best["dia"]:
                best["crop"], best["dia"], best["x"], best["y"] = crop, dia, x_, y_
            return
        clusters.append({"sig": s, "crop": crop, "dia": dia, "n": 1,
                         "ids": [fid], "x": x_, "y": y_})

    prod_stats = {"frames": 0, "agree": 0, "prod_misses": 0, "extra": 0}
    prod_miss_rows = []
    for vid in VIDEOS:
        path = VIDEO_DIR / vid
        cap = cv2.VideoCapture(str(path))
        if not cap.isOpened():
            print(f"video: cannot open {vid}")
            continue
        fps = cap.get(cv2.CAP_PROP_FPS) or 30
        total = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
        step = max(1, int(fps / 5))
        grid = None
        fid = 0
        while fid < total:
            cap.set(cv2.CAP_PROP_POS_FRAMES, fid)
            ok, f = cap.read()
            if not ok:
                break
            g = V.find_grid(f)
            if g is not None:
                grid = g
                print(f"video {vid}: grid locked at frame {fid} "
                      f"({g.x0},{g.y0})-({g.x1},{g.y1})")
                break
            fid += step
        if grid is None:
            print(f"video: no grid in {vid} over {total} frames - skipping")
            cap.release()
            continue

        gx0, gy0, gx1, gy1 = grid.x0, grid.y0, grid.x1, grid.y1
        by0 = max(0, gy0 - int(grid.width * 0.85))
        by1 = gy0
        bx0, bx1 = max(0, gx0 - 30), min(f.shape[1], gx1 + 30)
        sub_grid = V.Grid(x0=gx0 - bx0, y0=gy0 - by0, x1=gx1 - bx0,
                          y1=gy1 - by0, xs=[x - bx0 for x in grid.xs],
                          ys=[y - by0 for y in grid.ys])
        dmin = int(grid.width * 0.10)
        dmax = int(grid.width * 0.24)
        print(f"video {vid}: ball region x {bx0}..{bx1} y {by0}..{by1} "
              f"diameters {dmin}..{dmax}")
        while fid < total:
            cap.set(cv2.CAP_PROP_POS_FRAMES, fid)
            ok, f = cap.read()
            if not ok:
                break
            if fid % (step * 20) == 0:
                g = V.find_grid(f)
                if g is not None and (g.x0 != gx0 or g.y0 != gy0):
                    print(f"video {vid}: grid re-locked at frame {fid}")
                    grid = g
                    gx0, gy0, gx1, gy1 = grid.x0, grid.y0, grid.x1, grid.y1
                    by0 = max(0, gy0 - int(grid.width * 0.85))
                    by1 = gy0
                    bx0, bx1 = max(0, gx0 - 30), min(f.shape[1], gx1 + 30)
                    sub_grid = V.Grid(x0=gx0 - bx0, y0=gy0 - by0,
                                      x1=gx1 - bx0, y1=gy1 - by0,
                                      xs=[x - bx0 for x in grid.xs],
                                      ys=[y - by0 for y in grid.ys])
                    dmin = int(grid.width * 0.10)
                    dmax = int(grid.width * 0.24)
            region = f[by0:by1, bx0:bx1]
            cands = naive_balls(region, dmin, dmax)
            prod = V.find_balls(region, sub_grid, max_balls=3)
            prod_stats["frames"] += 1
            for (x, y, w, h) in cands:
                x_, y_ = x + bx0, y + by0
                crop = crop_of(f, x_, y_, w, h)
                if w * h > 0.5 * (dmax ** 2):
                    add(crop, fid, max(w, h))
            # production agreement check: every candidate that is nearly the
            # largest ball on screen should be seen by V.find_balls too
            mm = [cd for cd in cands if cd[2] >= dmax * 0.75]
            if cands and mm:
                for (x, y, w, h) in mm:
                    px, py = x + bx0, y + by0
                    hitc = sum(1 for b in prod if not (
                        b[0] + bx0 > px + w or b[0] + b[2] + bx0 < px
                        or b[1] + by0 > py + h or b[1] + b[3] + by0 < py))
                    if hitc == 0:
                        prod_stats["prod_misses"] += 1
                        prod_miss_rows.append(
                            [vid, fid, px, py, w, h])
            fid += step
        cap.release()

    print(f"video: {prod_stats['frames']} sampled frames, "
          f"{len(clusters)} unique ball appearances, "
          f"prod detector missed {prod_stats['prod_misses']} "
          f"large-ball candidate boxes")

    results = []
    for i, cl in enumerate(clusters):
        crop = cl["crop"]
        d = cl["dia"]
        fh, fw = crop.shape[:2]
        cx, cy = fw // 2, fh // 2
        bbox = (max(0, cx - d // 2), max(0, cy - d // 2), d, d)
        big = cv2.resize(crop, None, fx=2, fy=2, interpolation=cv2.INTER_CUBIC)
        bb2 = (bbox[0] * 2, bbox[1] * 2, bbox[2] * 2, bbox[3] * 2)
        b = V.read_ball_robust(engine, crop, bbox, min_score=0.92)
        b2 = V.read_ball_robust(engine, big, bb2, min_score=0.92)
        results.append({
            "id": i, "n_frames": cl["n"], "dia": int(d),
            "x": int(cl["x"]), "y": int(cl["y"]),
            "read": b["number"], "score": round(b["number_score"], 3),
            "read2x": b2["number"], "score2x": round(b2["number_score"], 3),
        })
        if b2["number"] != b["number"]:
            results[-1]["flag"] = "MISMATCH-SCALES"
        cv2.imwrite(str(OUT / f"ball_{i:03d}.png"), crop)
    with open(OUT / "video_balls.csv", "w", newline="", encoding="utf8") as f:
        w = csv.DictWriter(f, fieldnames=["id", "n_frames", "dia", "x", "y",
                                          "read", "score", "read2x", "score2x",
                                          "flag"])
        for r in results:
            w.writerow(r)

    # contact sheet
    from PIL import Image, ImageDraw, ImageFont
    cols = 8
    per = 78
    rowsn = (len(results) + cols - 1) // cols
    sheet = Image.new("RGB", (cols * per, rowsn * (per + 14)), (255, 255, 255))
    drw = ImageDraw.Draw(sheet)
    fnt = load_font(11)
    for i, r in enumerate(results):
        img = Image.open(OUT / f"ball_{r['id']:03d}.png")
        img.thumbnail((per - 4, per - 4))
        px = (i % cols) * per
        py = (i // cols) * (per + 14)
        sheet.paste(img, (px + 2, py + 2))
        drw.text((px + 2, py + per - 2),
                 f"#{i} {r['read']}/{r['read2x']} {r['n_frames']}f",
                 font=fnt, fill=(0, 0, 0))
    sheet.save(OUT / "balls_contactsheet.png")
    print(f"video: contact sheet + {len(results)} crops in {OUT}")
    labels = [(r["read"], r["read2x"]) for r in results]
    agree = sum(1 for a, b in labels if a is not None and a == b)
    read1 = sum(1 for a, _ in labels if a is not None)
    print(f"video: {read1}/{len(results)} clusters read at native size, "
          f"{agree} agree with the 2x read")


if __name__ == "__main__":
    t0 = time.time()
    mode = sys.argv[1] if len(sys.argv) > 1 else "cards"
    if mode == "cards":
        run_cards()
    elif mode == "balls":
        run_balls()
    elif mode == "video":
        run_video()
    else:
        raise SystemExit("unknown mode: " + mode)
    print(f"done in {time.time() - t0:.1f}s")
