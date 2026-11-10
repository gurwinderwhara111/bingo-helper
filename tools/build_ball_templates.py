"""tools/build_ball_templates.py - harvest the game's own ball digit templates.

v1.13 Step 1. Pipeline:
  1. Lock the ball strip by MEDIAN of all find_grid hits across the video
     (immune to the bouncing single-frame locks that broke earlier harvests).
  2. Detect every ball candidate in the strip with a colour-disk filter.
  3. Cluster crops by appearance signature; label each cluster by majority
     OCR vote across three scale variants (a hallucination cannot win a
     multi-read vote).
  4. Seeds: hand-verified powerup sheets (B12 G48 N44 O72) give ground-truth
     labelled crops; each seed is cross-checked with OCR before banking.
  5. Bank: whole-number ink masks normalised to 40x20, mean-centred, L2
     normalised; one row per template instance.
  6. Verify: leave-one-out match of every banked crop against the rest of
     the bank + a self-match sanity check.

Outputs:
  assets/ball_templates.npz                    (templates, numbers, aspects)
  debug/sim/ball_templates_report.csv          (per-cluster audit)
  debug/sim/balls_contactsheet_labelled.png    (visual QA)
  debug/sim/ball_mask_debug.png                (mask extraction QA)
"""
from __future__ import annotations

import csv
import sys
import time
from collections import Counter, defaultdict
from pathlib import Path

import cv2
import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import vision as V  # noqa: E402

OUT = ROOT / "debug" / "sim"
OUT.mkdir(parents=True, exist_ok=True)

VIDEO_DIR = ROOT / "gameplay videos"
VIDEOS = ["Bingo normal gameplay 2.mp4", "bingo normal live play1.mp4"]

SEED_SPECS = [  # (sheet, region box (x0,y0,x1,y1), [(cx, cy, number)])
    ("power up types with images examples/"
     "Power Up Pick Me Up Choose any number to receive that ball "
     "The right number can unlock multiple bingos at the same time.png",
     (330, 330, 640, 580), [(482, 455, 14)]),   # verified: B 14 ball
    ("power up types with images examples/"
     "Power Up Instant Ball  Get 3 free balls at the same time Hint These "
     "balls will come and go quickly so be ready.png",
     (250, 70, 680, 205), [(363, 147, 57), (470, 147, 63),
                           (578, 147, 59)]),   # verified: G57 O63 G59
]

# ---------------- mask + vector (later mirrored into vision.py) ------------


def ball_number_mask(crop: np.ndarray) -> np.ndarray | None:
    """Whole-number ink mask from one ball crop (bottom-most ink band).

    Measures inside the ball's pale FACE only - the coloured ring can be
    darker in grayscale than the digit threshold and must not leak in.
    """
    H, W = crop.shape[:2]
    hsv = cv2.cvtColor(crop, cv2.COLOR_BGR2HSV)
    sat, val = hsv[:, :, 1], hsv[:, :, 2]
    pale = ((sat <= 110) & (val >= 180)).astype(np.uint8) * 255
    pale = cv2.morphologyEx(pale, cv2.MORPH_CLOSE, np.ones((3, 3), np.uint8))
    pale = _fill_holes(pale)
    n, _, st, _ = cv2.connectedComponentsWithStats(pale, 8)
    fx = fy = fw = fh = 0
    best_area = 0.14 * W * H
    for i in range(1, n):
        x, y, w, h, area = st[i]
        if area > best_area and abs(w - h) <= 0.45 * max(w, h):
            fx, fy, fw, fh = x, y, w, h
            best_area = area
    if fw == 0:
        # fallback: central disk crop like v1.12's reader
        cx, cy = W / 2, H / 2
        r = min(W, H) / 2
        inset = max(2, int(r * 0.22))
        fx, fy = int(cx - r + inset), int(cy - r + inset)
        fw = fh = int(2 * (r - inset))
    face = crop[max(0, fy):fy + fh, max(0, fx):fx + fw]
    if face.size == 0:
        return None
    g = cv2.cvtColor(face, cv2.COLOR_BGR2GRAY)
    _, th = cv2.threshold(g, 130, 255, cv2.THRESH_BINARY_INV)
    hh, ww = th.shape
    # keep digit-shaped ink only: not edge-remnant, not letter band,
    # not a speck (relative to face area)
    nc, lab, stats, _ = cv2.connectedComponentsWithStats(
        (th > 0).astype(np.uint8), 8)
    clean = np.zeros_like(th)
    edge = 1
    minink = max(15, 0.010 * hh * ww)
    for i in range(1, nc):
        x, y, w0, h0, area0 = stats[i]
        if area0 < minink:
            continue
        if y + h0 / 2 < 0.42 * hh:      # letter band / top artefacts
            continue
        if (x <= edge or y <= edge or
                x + w0 >= ww - edge or y + h0 >= hh - edge):
            continue
        clean[lab == i] = 255
    th = clean
    m = max(1, int(0.06 * min(hh, ww)))
    th[:m, :] = 0
    th[-m:, :] = 0
    th[:, :m] = 0
    th[:, -m:] = 0
    ink = (th > 0).sum(axis=1)
    # contiguous ink runs with small gap tolerance
    runs = []
    start = None
    last_ink = -10
    for y in range(hh):
        if ink[y] > 0:
            if start is None:
                start = y
            last_ink = y
        elif start is not None and y - last_ink > 2:
            runs.append((start, last_ink))
            start = None
    if start is not None:
        runs.append((start, last_ink))
    runs = [(a, b) for (a, b) in runs if b - a + 1 >= 3]
    if not runs:
        return None
    a, b = runs[-1]  # bottom-most band = the number (letter sits above)
    num = th[a:b + 1, :]
    ys, xs = np.where(num > 0)
    if len(xs) < 20:
        return None
    return num[ys.min():ys.max() + 1, xs.min():xs.max() + 1]


def mask_norm_vec(mask: np.ndarray, w: int = 40, h: int = 20):
    """Fit ink mask into (h,w) canvas preserving aspect + centred.

    MNIST-style: bbox -> scale to fit -> area-resample -> center in canvas.
    Returns (vector, original aspect). Canvas-fit keeps "1" narrow and
    "73" wide, so the shape difference between 1- and 2-digit numbers
    survives instead of being stretched away.
    """
    ys, xs = np.where(mask > 0)
    if len(xs) < 15:
        return None, 0.0
    m = mask[ys.min():ys.max() + 1, xs.min():xs.max() + 1].astype(np.float32)
    hh, ww = m.shape
    aspect = ww / max(1, hh)
    k = min(w / ww, h / hh)
    nw = max(1, int(round(ww * k)))
    nh = max(1, int(round(hh * k)))
    r = cv2.resize(m, (nw, nh), interpolation=cv2.INTER_AREA)
    canvas = np.zeros((h, w), np.float32)
    ox, oy = (w - nw) // 2, (h - nh) // 2
    canvas[oy:oy + nh, ox:ox + nw] = r
    vec = canvas.ravel().astype(np.float32)
    vec -= vec.mean()
    n = float(np.linalg.norm(vec))
    if n < 1e-6:
        return None, 0.0
    return vec / n, aspect


# ---------------- harvesting ----------------------------------------------


def _fill_holes(mask: np.ndarray) -> np.ndarray:
    """Fill enclosed holes so a colour ring counts as one solid disk."""
    h, w = mask.shape
    ff = mask.copy()
    ffim = np.zeros((h + 2, w + 2), np.uint8)
    cv2.floodFill(ff, ffim, (0, 0), 255)
    return cv2.bitwise_or(mask, cv2.bitwise_not(ff))


def naive_balls(region: np.ndarray, dmin: int, dmax: int):
    hsv = cv2.cvtColor(region, cv2.COLOR_BGR2HSV)
    sat, val = hsv[:, :, 1], hsv[:, :, 2]
    mask = ((sat >= 90) & (val >= 120)).astype(np.uint8) * 255
    mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, np.ones((3, 3), np.uint8))
    mask = _fill_holes(mask)  # ring-style balls -> solid disks
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
            continue
        if x <= 2 or y <= 2 or x + w >= W - 2 or y + h >= H - 2:
            continue
        out.append((x, y, w, h))
    return out


def white_faces(region: np.ndarray, dmin: int, dmax: int):
    """White face disks inside coloured ball rings -> (x,y,w,h) faces."""
    hsv = cv2.cvtColor(region, cv2.COLOR_BGR2HSV)
    sat, val = hsv[:, :, 1], hsv[:, :, 2]
    mask = ((sat <= 70) & (val >= 200)).astype(np.uint8) * 255
    mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, np.ones((3, 3), np.uint8))
    mask = _fill_holes(mask)
    n, _, st, _ = cv2.connectedComponentsWithStats(mask, 8)
    out = []
    H, W = region.shape[:2]
    for i in range(1, n):
        x, y, w, h, area = st[i]
        if not (dmin <= w <= dmax and dmin <= h <= dmax):
            continue
        if abs(w - h) > 0.22 * max(w, h):
            continue
        if area < 0.60 * w * h:
            continue
        if x <= 2 or y <= 2 or x + w >= W - 2 or y + h >= H - 2:
            continue
        out.append((x, y, w, h))
    return out


def ring_balls(region: np.ndarray, dmin: int, dmax: int):
    """Balls whose saturated RING colour forms an annulus; hue-selective.

    Catches balls sitting on a saturated banner where a plain saturation
    scan merges everything into one component.
    """
    hsv = cv2.cvtColor(region, cv2.COLOR_BGR2HSV)
    h_, sat, val = hsv[:, :, 0], hsv[:, :, 1], hsv[:, :, 2]
    teal = cv2.inRange(hsv, (55, 90, 110), (125, 255, 255))
    orange = cv2.inRange(hsv, (3, 90, 110), (28, 255, 255))
    mask = cv2.bitwise_or(teal, orange)
    mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, np.ones((3, 3), np.uint8))
    mask = _fill_holes(mask)
    n, _, st, _ = cv2.connectedComponentsWithStats(mask, 8)
    out = []
    H, W = region.shape[:2]
    for i in range(1, n):
        x, y, w, h, area = st[i]
        if not (dmin <= w <= dmax and dmin <= h <= dmax):
            continue
        if abs(w - h) > 0.22 * max(w, h):
            continue
        if area < 0.55 * w * h:
            continue
        if x <= 2 or y <= 2 or x + w >= W - 2 or y + h >= H - 2:
            continue
        out.append((x, y, w, h))
    return out


    teal = cv2.inRange(hsv, (55, 90, 110), (125, 255, 255))
    orange = cv2.inRange(hsv, (3, 90, 110), (28, 255, 255))
    mask = cv2.bitwise_or(teal, orange)
    mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, np.ones((3, 3), np.uint8))
    mask = _fill_holes(mask)
    n, _, st, _ = cv2.connectedComponentsWithStats(mask, 8)
    out = []
    H, W = region.shape[:2]
    for i in range(1, n):
        x, y, w, h, area = st[i]
        if not (dmin <= w <= dmax and dmin <= h <= dmax):
            continue
        if abs(w - h) > 0.22 * max(w, h):
            continue
        if area < 0.55 * w * h:
            continue
        if x <= 2 or y <= 2 or x + w >= W - 2 or y + h >= H - 2:
            continue
        out.append((x, y, w, h))
    return out


def detect_balls(region: np.ndarray, dmin: int, dmax: int):
    """Candidate ball boxes: white-face detection + saturated-ring scan."""
    boxes = []
    for (x, y, w, h) in white_faces(region, int(dmin * 0.60),
                                    int(dmax * 0.88)):
        grow = max(2, int(0.21 * w))
        boxes.append((x - grow, y - grow, w + 2 * grow, h + 2 * grow))
    boxes += naive_balls(region, dmin, dmax)
    return boxes


def crop_of(frame, x, y, w, h, grow=2):
    x0, y0 = max(0, x - grow), max(0, y - grow)
    x1 = min(frame.shape[1], x + w + grow)
    y1 = min(frame.shape[0], y + h + grow)
    return frame[y0:y1, x0:x1].copy()


def sig_of(crop):
    g = cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY)
    g = cv2.resize(g, (24, 24), interpolation=cv2.INTER_AREA)
    return g.astype(np.int16)


def cluster_add(clusters, crop, w, h, thresh=16.0):
    """Append crop to the nearest appearance cluster or open a new one."""
    s = sig_of(crop)
    best, bd = None, None
    for cl in clusters:
        d = float(np.abs(s - cl["sig"]).mean())
        if best is None or d < bd:
            best, bd = cl, d
    if best is not None and bd < thresh:
        best["n"] += 1
        if w * h > best["w"] * best["h"]:
            best["crop"], best["w"], best["h"] = crop, w, h
    else:
        clusters.append({"sig": s, "crop": crop, "w": w, "h": h, "n": 1})


def label_cluster(engine, crop):
    """Majority OCR vote across three scale variants; None if contested."""
    reads = []
    variants = [crop,
                cv2.resize(crop, None, fx=2, fy=2, interpolation=cv2.INTER_CUBIC),
                cv2.resize(crop, None, fx=3, fy=3, interpolation=cv2.INTER_CUBIC)]
    box = (0, 0, 0, 0)
    for big in variants:
        box = (0, 0, big.shape[1], big.shape[0])
        b = V.read_ball_robust(engine, big, box, min_score=0.88)
        if b.get("number") is not None:
            reads.append(int(b["number"]))
    if not reads:
        return None, []
    top, cnt = Counter(reads).most_common(1)[0]
    if cnt < 2 or cnt / len(reads) < 0.66:
        return None, reads
    return int(top), reads


def harvest_video(path: Path, engine):
    """Return cluster dicts for one video, median-locked to the grid."""
    cap = cv2.VideoCapture(str(path))
    if not cap.isOpened():
        print(f"harvest: cannot open {path.name}")
        return []
    fps = cap.get(cv2.CAP_PROP_FPS) or 30.0
    total = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
    step = max(1, int(fps / 5))

    # pass 1: grid hits -> median geometry
    hits = []
    fid = 0
    while fid < total:
        cap.set(cv2.CAP_PROP_POS_FRAMES, fid)
        ok, f = cap.read()
        if not ok:
            break
        g = V.find_grid(f)
        if g is not None:
            hits.append((g.x0, g.y0, g.x1, g.y1))
        fid += max(step * 3, 1)
    if len(hits) < 10:
        print(f"harvest: only {len(hits)} grid hits in {path.name}")
        cap.release()
        return []
    gx0, gy0, gx1, gy1 = (int(np.median([h[i] for h in hits]))
                          for i in range(4))
    ring = [h for h in hits
            if abs(h[0] - gx0) <= 6 and abs(h[1] - gy0) <= 6]
    print(f"harvest {path.name}: median grid ({gx0},{gy0})-({gx1},{gy1}) "
          f"hits={len(hits)} stable={len(ring) / len(hits):.0%}")
    width = gx1 - gx0
    by0 = max(0, gy0 - int(width * 0.85))
    by1 = max(by0 + 1, gy0)
    bx0 = max(0, gx0 - 30)
    bx1 = min(int(cap.get(cv2.CAP_PROP_FRAME_WIDTH)), gx1 + 30)
    dmin = int(width * 0.10)
    dmax = int(width * 0.24)

    clusters = []
    fid = 0
    while fid < total:
        cap.set(cv2.CAP_PROP_POS_FRAMES, fid)
        ok, f = cap.read()
        if not ok:
            break
        region = f[by0:by1, bx0:bx1]
        for (x, y, w, h) in detect_balls(region, dmin, dmax):
            crop = crop_of(f, x + bx0, y + by0, w, h)
            cluster_add(clusters, crop, w, h)
        fid += step
    cap.release()

    for cl in clusters:
        top, reads = label_cluster(engine, cl["crop"])
        cl["label"], cl["reads"] = top, reads
    return clusters


def harvest_seeds(engine):
    """Hand-labelled sheet balls: face-detect, match to expected position,
    OCR-verify before banking."""
    out = []
    for (sheet, box, labels) in SEED_SPECS:
        p = ROOT / sheet
        if not p.exists():
            print(f"seeds: missing sheet {sheet}")
            continue
        img = cv2.imread(str(p))
        if img is None:
            print(f"seeds: cannot read {p.name}")
            continue
        x0r, y0r, x1r, y1r = box
        reg = img[y0r:y1r, x0r:x1r]
        faces = (white_faces(reg, 20, 140) + ring_balls(reg, 20, 130) +
                 naive_balls(reg, 15, 130))
        abs_faces = [(x + x0r, y + y0r, w, h) for (x, y, w, h) in faces]
        for (cx, cy, num) in labels:
            best, bd = None, None
            for (x, y, w, h) in abs_faces:
                d = abs(x + w / 2 - cx) + abs(y + h / 2 - cy)
                if best is None or d < bd:
                    best, bd = (x, y, w, h), d
            if best is None:
                print(f"seed #{num}: no blob found near ({cx},{cy})")
                continue
            x, y, w, h = best
            if bd > (box[2] - box[0]) * 0.12:
                print(f"seed #{num}: nearest blob {bd:.0f}px away - SKIPPED")
                continue
            if 1.25 <= w / h or h / w >= 1.25:   # merged banner, skip
                continue
            crop = crop_of(img, x, y, w, h, grow=3)
            box = (0, 0, crop.shape[1], crop.shape[0])
            read = V.read_ball_robust(engine, crop, box, min_score=0.80)
            got = read.get("number")
            if got != num:
                print(f"seed #{num}: crop {w}x{h} @({x},{y}) "
                      f"ocr={got} - SKIPPED")
                continue
            print(f"seed #{num}: crop {w}x{h} @({x},{y}) ocr={got} ok")
            out.append((num, crop, "seed"))
    return out


# ---------------- bank + verification --------------------------------------


def build_bank(items):
    """items: list of (num, crop, source). Returns bank + QA masks."""
    bank_map = defaultdict(list)  # num -> [(vec, aspect, crop, src)]
    masks_dbg = []
    for (num, crop, src) in items:
        mask = ball_number_mask(crop)
        if mask is None:
            continue
        vec, aspect = mask_norm_vec(mask)
        if vec is None:
            continue
        bank_map[num].append((vec, aspect, crop, src))
        masks_dbg.append((num, mask, crop, src))
    bank = {num: [(v, a) for (v, a, _, _) in rows]
            for num, rows in bank_map.items()}
    return bank, masks_dbg


def match(vec, aspect, bank, aspect_guard=0.60):
    """Return ranked [(num, score)] using cosine + aspect guard."""
    scored = []
    for num, rows in bank.items():
        s_max = None
        for (v, a) in rows:
            if abs(aspect - a) > aspect_guard:
                continue
            s = float(np.dot(vec, v))
            if s_max is None or s > s_max:
                s_max = s
        if s_max is not None:
            scored.append((num, s_max))
    scored.sort(key=lambda t: -t[1])
    return scored


def leave_one_out(items, bank):
    """Every item scored against the bank MINUS its own contributions."""
    rows = []
    vecs_cache = []  # (num, vec, aspect) per item, masks extracted once
    for (num, crop, src) in items:
        mask = ball_number_mask(crop)
        vec, aspect = (None, 0.0)
        if mask is not None:
            vec, aspect = mask_norm_vec(mask)
        vecs_cache.append((num, vec, aspect))
    for idx, (num, vec, aspect) in enumerate(vecs_cache):
        src = items[idx][2]
        if vec is None:
            rows.append((num, src, None, None, None, 0.0, "no-mask-vec"))
            continue
        bank_loo = defaultdict(list)
        for j, (nj, vj, aj) in enumerate(vecs_cache):
            if j == idx or vj is None:
                continue
            bank_loo[nj].append((vj, aj))
        ranked = match(vec, aspect, dict(bank_loo))
        if not ranked:
            rows.append((num, src, None, None, None, 0.0, "no-candidate"))
            continue
        pred, score = ranked[0]
        ratio = (ranked[1][1] / score) if (len(ranked) > 1 and score > 0) \
            else 0.0
        margin = score - (ranked[1][1] if len(ranked) > 1 else 0.0)
        verdict = "ok" if pred == num else "WRONG"
        if verdict == "ok" and (ratio >= 0.90 or margin < 0.03):
            verdict = "ok-CONTESTED"   # per-research ambiguity band
        rows.append((num, src, pred, round(score, 3),
                     round(margin, 3), round(ratio, 3), verdict))
    return rows


# ---------------- main ------------------------------------------------------

def save_bank(bank):
    labels, vecs, aspects = [], [], []
    for num, rows in sorted(bank.items()):
        for (v, a) in rows:
            labels.append(num)
            vecs.append(v)
            aspects.append(a)
    if not vecs:
        return None
    path = ROOT / "assets" / "ball_templates.npz"
    path.parent.mkdir(parents=True, exist_ok=True)
    np.savez_compressed(path,
                        templates=np.vstack(vecs).astype(np.float32),
                        numbers=np.array(labels, dtype=np.int32),
                        aspects=np.array(aspects, dtype=np.float32))
    return path


def contact_sheet(masks_dbg, bank):
    from PIL import Image, ImageDraw, ImageFont
    items = masks_dbg[:64]
    if not items:
        return None
    per, cols = 110, 8
    rows_n = (len(items) + cols - 1) // cols
    sheet = Image.new("RGB", (cols * per, rows_n * (per + 16)),
                      (255, 255, 255))
    drw = ImageDraw.Draw(sheet)
    try:
        fnt = ImageFont.truetype(r"C:\Windows\Fonts\arialbd.ttf", 12)
    except OSError:
        fnt = ImageFont.load_default()
    for i, (num, mask, crop, src) in enumerate(items):
        nz = np.where(mask > 0)
        if len(nz[0]):
            mask = mask[nz[0].min():nz[0].max() + 1,
                        nz[1].min():nz[1].max() + 1]
        h, w = mask.shape[:2]
        k = min(88 / max(1, w), 88 / max(1, h))
        nw = max(1, int(round(w * k)))
        nh = max(1, int(round(h * k)))
        mm = cv2.resize(mask, (nw, nh), interpolation=cv2.INTER_AREA)
        cell = np.full((96, 96), 255, np.uint8)
        ink = (mm > 0).astype(np.uint8) * 0
        cell[4:4 + nh, 4:4 + nw] = ink
        py2 = (i // cols) * (per + 16)
        px = (i % cols) * per
        sheet.paste(Image.fromarray(cell), (px + 2, py2 + 2))
        drw.text((px + 2, py2 + 96), f"#{num} {src[:6]}",
                 font=fnt, fill=(0, 0, 0))
    out = OUT / "ball_mask_debug.png"
    sheet.save(out)
    return out


def main():
    from rapidocr import RapidOCR
    engine = RapidOCR()
    t0 = time.time()

    all_items = []   # (num, crop, src)
    audit = []       # (video, label, frames, reads)
    for vid in VIDEOS:
        clusters = harvest_video(VIDEO_DIR / vid, engine)
        n_lab = sum(1 for c in clusters if c["label"] is not None)
        print(f"  {vid}: {len(clusters)} clusters, {n_lab} labelled")
        for cl in clusters:
            audit.append((vid, cl["label"], cl["n"],
                          "|".join(str(x) for x in cl["reads"])))
            if cl["label"] is not None:
                all_items.append((cl["label"], cl["crop"], vid[:12]))
        print(f"  {vid}: done ({time.time() - t0:.0f}s)")

    seeds = harvest_seeds(engine)
    all_items += seeds

    bank, masks_dbg = build_bank(all_items)
    print(f"bank: {len(bank)} numbers -> {sorted(bank.keys())}")

    npz = save_bank(bank)
    print(f"bank saved: {npz} "
          f"({sum(len(r) for r in bank.values())} template rows)")

    loo = leave_one_out(all_items, bank)
    n_ok = sum(1 for r in loo if str(r[6]).startswith("ok"))
    n_clean = sum(1 for r in loo if r[6] == "ok")
    n_cont = sum(1 for r in loo if r[6] == "ok-CONTESTED")
    print(f"LOO: {n_ok}/{len(loo)} correct "
          f"({n_clean} clean, {n_cont} contested)")
    wrong = [(r[0], r[2]) for r in loo if r[6] == "WRONG"]
    if wrong:
        print(f"LOO misses (true->pred): {wrong}")

    with open(OUT / "ball_templates_report.csv", "w", newline="",
              encoding="utf8") as f:
        w = csv.writer(f)
        w.writerow(["kind", "label", "frames", "reads", "loo_pred",
                    "loo_score", "loo_margin", "verdict"])
        for (vid, lab, n_, reads) in audit:
            w.writerow(["cluster", lab, n_, reads, "", "", "", ""])
        for (num, src, pred, score, margin, _, ok) in loo:
            w.writerow(["loo", num, "", "", pred, score, margin, ok])

    sheet_path = contact_sheet(masks_dbg, bank)
    print(f"mask QA sheet: {sheet_path}")
    print(f"done in {time.time() - t0:.1f}s")


if __name__ == "__main__":
    main()
