"""tools/eval_screenshots.py - evaluate the tool on the user's screenshot
folder ONLY (user directive: no videos, no other images).

For every PNG in that folder:
  1. V.find_grid / V.rectify / V.read_card_auto / V.validate_card
     -> the production card/grid read, exactly as the helper does it.
  2. V._ball_candidates -> production ball boxes above the card
     -> V.read_ball_robust (OCR, production) AND the v1.13 candidate
        template reader (assets/ball_templates.npz, reported separately).
  3. Saves an annotated view + one JSON row per file.

Truth is entered by hand (human view of the raw screenshots) in
debug/eval/truth.json, then `python tools/eval_screenshots.py compare`
scores tool vs truth.

Usage:
  python tools/eval_screenshots.py run      # produce tool claims
  python tools/eval_screenshots.py compare  # score against truth.json
"""
from __future__ import annotations

import json
import sys
from collections import Counter, defaultdict
from pathlib import Path

import cv2
import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import vision as V  # noqa: E402

FOLDER = ROOT / ("screenshots for traning on when balls comes and the "
                 "diff diff drid numner and ball numbers")
OUT = ROOT / "debug" / "eval"
BALLDIR = OUT / "balls"
ANN = OUT / "annotated"
for d in (OUT, BALLDIR, ANN):
    d.mkdir(parents=True, exist_ok=True)

FILES = sorted(FOLDER.glob("*.png"))
SHORT = {p: f"e{i:02d}" for i, p in enumerate(FILES)}


# ---------------- template reader (v1.13 candidate, self-contained) --------

def mask_norm_vec(mask, w=40, h=20):
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


def _fill_holes(mask):
    h, w = mask.shape
    ff = mask.copy()
    ffim = np.zeros((h + 2, w + 2), np.uint8)
    cv2.floodFill(ff, ffim, (0, 0), 255)
    return cv2.bitwise_or(mask, cv2.bitwise_not(ff))


def ball_number_mask(crop):
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
    nc, lab, stats, _ = cv2.connectedComponentsWithStats(
        (th > 0).astype(np.uint8), 8)
    clean = np.zeros_like(th)
    edge = 1
    minink = max(15, 0.010 * hh * ww)
    for i in range(1, nc):
        x, y, w0, h0, area0 = stats[i]
        if area0 < minink:
            continue
        if y + h0 / 2 < 0.42 * hh:
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
    a, b = runs[-1]
    num = th[a:b + 1, :]
    ys, xs = np.where(num > 0)
    if len(xs) < 20:
        return None
    return num[ys.min():ys.max() + 1, xs.min():xs.max() + 1]


_TPL = {}


def load_templates():
    if _TPL:
        return _TPL
    npz = ROOT / "assets" / "ball_templates.npz"
    if not npz.exists():
        return _TPL
    d = np.load(npz)
    _TPL["T"] = d["templates"].astype(np.float32)
    _TPL["N"] = d["numbers"].astype(np.int32)
    _TPL["A"] = d["aspects"].astype(np.float32)
    return _TPL


def read_ball_template(img, bbox, aspect_guard=0.60):
    T = load_templates()
    if not T:
        return None, 0.0, None, 0.0
    x, y, w, h = [int(v) for v in bbox]
    crop = img[max(0, y):y + h, max(0, x):x + w]
    if crop.size == 0:
        return None, 0.0, None, 0.0
    mask = ball_number_mask(crop)
    vec, aspect = (None, 0.0) if mask is None else mask_norm_vec(mask)
    if vec is None:
        return None, 0.0, None, 0.0
    scores = []
    Ts = T["T"]
    hit = np.abs(T["A"] - aspect) <= aspect_guard
    if hit.sum() == 0:
        return None, 0.0, None, 0.0
    sub = Ts[hit]
    dots = sub @ vec
    best_idx = int(np.argmax(dots))
    best_score = float(dots[best_idx])
    nums = T["N"][hit]
    best_num = int(nums[best_idx])
    # second best OTHER number
    others = dots.copy()
    others[nums == best_num] = -2.0
    j2 = int(np.argmax(others))
    s2 = float(others[j2]) if others[j2] > -1.5 else None
    n2 = int(nums[j2]) if others[j2] > -1.5 else None
    return best_num, best_score, n2, s2


def letter_of(num):
    if num is None:
        return "?"
    if num <= 15:
        return "B"
    if num <= 30:
        return "I"
    if num <= 45:
        return "N"
    if num <= 60:
        return "G"
    return "O"


# ---------------- run -------------------------------------------------------

def iou(a, b):
    ax0, ay0, ax1, ay1 = a
    bx0, by0, bx1, by1 = b
    ix = max(0, min(ax1, bx1) - max(ax0, bx0))
    iy = max(0, min(ay1, by1) - max(ay0, by0))
    inter = ix * iy
    ua = (ax1 - ax0) * (ay1 - ay0) + (bx1 - bx0) * (by1 - by0) - inter
    return inter / ua if ua > 0 else 0.0


def run():
    from rapidocr import RapidOCR
    engine = RapidOCR()
    results = {}
    for p in FILES:
        img = cv2.imread(str(p))
        if img is None:
            results[SHORT[p]] = {"file": p.name, "error": "cannot read"}
            continue
        H, W = img.shape[:2]
        row = {"file": p.name, "size": [W, H], "balls": [], "popup": None}
        grid = V.find_grid(img)
        if grid is None:
            row["grid"] = None
        else:
            row["grid"] = [int(grid.x0), int(grid.y0),
                           int(grid.x1), int(grid.y1)]
            warped = V.rectify(img, grid)
            if warped is None:
                row["cells"] = None
                row["card_valid"] = False
            else:
                card, scores, free = V.read_card_auto(engine, warped,
                                                      daubed=set(), old=None)
                row["cells"] = (None if card is None else
                                [[int(v) if v is not None else None
                                  for v in r] for r in card])
                row["cell_scores"] = (None if scores is None else
                                      [[round(float(s), 2)
                                        if s is not None else None
                                        for s in r] for r in scores])
                issues = V.validate_card(card, free) if card else ["no-card"]
                row["card_issues"] = issues
                row["card_valid"] = not issues
        for ent in (V._ball_candidates(img, grid)
                    if grid is not None else []):
            bx, by, bw, bh = ent[0], ent[1], ent[2], ent[3]
            crop = img[max(0, by):by + bh, max(0, bx):bx + bw]
            b = V.read_ball_robust(engine, img, (bx, by, bw, bh),
                                   min_score=0.88)
            onum = b.get("number")
            oscore = round(float(b.get("number_score") or 0.0), 3)
            tnum, tscore, tnum2, tscore2 = read_ball_template(
                img, (bx - 1, by - 1, bw + 2, bh + 2))
            row["balls"].append({
                "bbox": [int(bx), int(by), int(bw), int(bh)],
                "ocr": (int(onum) if onum is not None and
                        1 <= onum <= 75 else None),
                "ocr_score": oscore,
                "tpl": (int(tnum) if tnum is not None else None),
                "tpl_score": round(tscore, 3) if tnum is not None else None,
                "tpl2": (int(tnum2) if tnum2 is not None else None),
                "tpl2_score": (round(tscore2, 3) if tnum2 is not None
                               else None),
            })
        # dedupe overlapping boxes, keep the first (sorted below)
        keep = []
        for b1 in row["balls"]:
            x1, y1, w1, h1 = b1["bbox"]
            dup = False
            for b2 in keep:
                x2, y2, w2, h2 = b2["bbox"]
                if iou((x1, y1, x1 + w1, y1 + h1),
                       (x2, y2, x2 + w2, y2 + h2)) > 0.35:
                    dup = True
                    break
            if not dup:
                keep.append(b1)
        keep.sort(key=lambda b: (b["bbox"][1] + b["bbox"][3] / 2,
                                 b["bbox"][0]))
        row["balls"] = keep
        if keep:
            ci = max(range(len(keep)),
                     key=lambda i: keep[i]["bbox"][2] * keep[i]["bbox"][3])
            row["current_idx"] = ci
        results[SHORT[p]] = row

        # save ball crops for later adjudication / bank expansion
        for i, b in enumerate(row["balls"]):
            x, y, w, h = b["bbox"]
            crop = img[max(0, y):y + h, max(0, x):x + w]
            if crop.size:
                cv2.imwrite(str(BALLDIR / f"{SHORT[p]}_b{i}.png"), crop)
                mask = ball_number_mask(crop)
                if mask is not None and mask.size:
                    mm = cv2.resize(mask, (mask.shape[1] * 6,
                                           mask.shape[0] * 6),
                                    interpolation=cv2.INTER_NEAREST)
                    cv2.imwrite(str(BALLDIR /
                                    f"{SHORT[p]}_b{i}_mask.png"),
                                255 - (mm > 0).astype(np.uint8) * 255)

        # annotated view (small, for human check)
        view = img.copy()
        if grid is not None:
            cv2.rectangle(view, (grid.x0, grid.y0),
                          (grid.x1, grid.y1), (0, 200, 0), 2)
            cells = row.get("cells")
            if cells:
                for r in range(5):
                    for c in range(5):
                        v = cells[r][c]
                        if v is None:
                            continue
                        x0, y0 = grid.xs[c], grid.ys[r]
                        cv2.putText(view, f"r{r+1}c{c+1}={v}",
                                    (x0 + 2, y0 + 16), 0, 0.42,
                                    (255, 60, 0), 1, cv2.LINE_AA)
        for i, b in enumerate(row["balls"]):
            x, y, w, h = b["bbox"]
            cv2.rectangle(view, (x, y), (x + w, y + h), (0, 0, 255), 2)
            tpl_s = "-" if b["tpl_score"] is None else f"{b['tpl_score']:.3f}"
            txt = (f"i{y}x{x}: ocr={b['ocr']}({b['ocr_score']})"
                   f" tpl={b['tpl']}({tpl_s})")
            cv2.putText(view, txt, (x, max(20, y - 6)), 0, 0.46,
                        (0, 0, 255), 1, cv2.LINE_AA)
        k = min(1.0, 1280 / max(1, view.shape[1]))
        if k < 1.0:
            view = cv2.resize(view, None, fx=k, fy=k,
                              interpolation=cv2.INTER_AREA)
        cv2.imwrite(str(ANN / f"{SHORT[p]}_ann.png"), view)
    (OUT / "tool_results.json").write_text(
        json.dumps(results, indent=1), encoding="utf8")
    print("locked grids:",
          sum(1 for r in results.values() if r.get("grid")),
          "/", len(results))
    nb = sum(len(r.get("balls") or []) for r in results.values())
    print("ball candidates:", nb)
    ok = sum(1 for r in results.values() for b in r.get("balls") or []
             if b["ocr"] is not None)
    tpl_ok = sum(1 for r in results.values() for b in r.get("balls") or []
                 if b["tpl"] is not None)
    print("ball OCR reads:", ok, " template reads:", tpl_ok)
    print("cells valid cards:",
          sum(1 for r in results.values() if r.get("card_valid")))
    print("claims ->", OUT / "tool_results.json")


def _load_truth():
    tp = OUT / "truth.json"
    if not tp.exists():
        print("no truth.json - build it by viewing the raw screenshots first")
        return None
    return json.loads(tp.read_text(encoding="utf8", errors="replace"))


def compare():
    claims = json.loads((OUT / "tool_results.json").read_text(
        encoding="utf8"))
    truth = _load_truth()
    if truth is None:
        return
    details = []
    ball_hit = ball_miss = 0
    junk = Counter()
    phantom_lock = 0
    votes = {"ocr_only": 0, "both": 0, "junk-ocr": 0, "junk-tpl": 0}
    tpl_only_hits = 0
    cell_hit = cell_wrong = cell_total = 0
    cell_conf = Counter()
    grid_fail = []
    for sid, t in truth.items():
        c = claims.get(sid)
        if c is None:
            print("no claims for", sid)
            continue
        if t.get("locked") is False and c.get("grid") is not None:
            phantom_lock += 1
            details.append({"file": sid, "phantom_lock": c["grid"]})
        if t.get("locked") is False:
            continue
        # ---- multiset coverage: any box reading a truth value covers one
        # instance of it; leftover reads are junk; leftover truth = uncovered.
        inst = [int(t["current"][1:])]
        inst += [int(v[1:]) for v in (t.get("strip") or [])]
        rem = Counter(inst)
        for b in (c.get("balls") or []):
            onum, tnum = b["ocr"], b["tpl"]
            o_hit = t_hit = False
            if onum is not None:
                if rem[onum] > 0:
                    rem[onum] -= 1
                    o_hit = True
                else:
                    votes["junk-ocr"] += 1
                    junk[onum] += 1
                    details.append({"file": sid, "kind": "junk-ocr",
                                    "value": onum, "bbox": b["bbox"]})
            if tnum is not None and rem[tnum] > 0:
                rem[tnum] -= 1
                t_hit = True
            if o_hit and t_hit:
                votes["both"] += 1
            elif o_hit:
                votes["ocr_only"] += 1
            elif t_hit:
                tpl_only_hits += 1
        cov = len(inst) - sum(rem.values())
        ball_hit += cov
        ball_miss += sum(rem.values())
        for val, n in rem.items():
            for _ in range(n):
                details.append({"file": sid, "kind": "uncovered",
                                "truth": val})
        truth_cells = t.get("cells")
        tool_cells = c.get("cells")
        if c.get("grid") is None:
            grid_fail.append(sid)
            if truth_cells:
                for row in truth_cells:
                    for tv in row:
                        if tv is not None:
                            cell_total += 1
                            cell_wrong += 1
            continue
        if not truth_cells:
            continue
        if tool_cells is None:
            for row in truth_cells:
                for tv in row:
                    if tv is not None:
                        cell_total += 1
                        cell_wrong += 1
            continue
        for r in range(min(5, len(truth_cells))):
            for cc in range(min(5, len(truth_cells[r]))):
                tv = truth_cells[r][cc]
                if tv is None:
                    continue
                cell_total += 1
                got = tool_cells[r][cc] if (
                    tool_cells and r < len(tool_cells)) else None
                if got == tv:
                    cell_hit += 1
                else:
                    cell_wrong += 1
                    cell_conf[f"{got}->{tv}"] += 1
                    details.append({"file": sid, "kind": "cell-wrong",
                                    "r": r + 1, "c": cc + 1,
                                    "truth": tv, "tool": got})
    print("\n=== BALLS (multiset coverage) ===")
    tot = ball_hit + ball_miss
    print(f"truth instances: {tot}  covered {ball_hit}  uncovered "
          f"{ball_miss}  ({ball_hit / max(1, tot):.1%})")
    print(f"reader contribution: ocr+tpl agree {votes['both']}, "
          f"ocr-only {votes['ocr_only']}, tpl-only {tpl_only_hits}")
    print(f"junk reads (no such truth value in file): "
          f"ocr {votes['junk-ocr']} -> {dict(junk)}")
    print("\n=== GRID ===")
    print(f"cells scored: {cell_total}  hit {cell_hit}  wrong {cell_wrong}")
    if cell_total:
        print(f"grid accuracy: {cell_hit / cell_total:.1%}")
    if cell_conf:
        top = ", ".join(f"{k}(x{v})" for k, v in cell_conf.most_common(15))
        print("cell confusions:", top)
    if grid_fail:
        print("files locked-failed:", grid_fail)
    if phantom_lock:
        print("phantom locks (tool locked, human says no card):",
              phantom_lock)
    (OUT / "compare_details.json").write_text(
        json.dumps(details, indent=1), encoding="utf8")
    print("details ->", OUT / "compare_details.json")


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "compare":
        compare()
    else:
        run()
