"""tools/test_ballpick.py - REAL pipeline test for the ball-selection redesign.

Runs the production detector on the user's 28 screenshot files, measures
identity validators on every candidate box, then scores selection rules:

  OLD (v1.12):          largest candidate wins
  NEW zone+size:        candidates with cx >= x0 + zone_frac * gridwidth
                        and diameter in [0.8, 1.35] * cell, rightmost wins
  NEW + ring validator: additionally the box must show a saturated colour
                        ring (ball identity: teal/purple/blue/orange)
  NEW + face validator: additionally a pale, round face must exist inside
                        the box (extractor's face locator)

Also tested: threshold sensitivity (zone_frac), centre-proximity clustering
for duplicate boxes, and per-file QA renders with guide lines.

Validator samples: PHANTOM boxes are the ones I verified visually in
debug/eval/phantom_montage.png; REAL boxes are the confirmed ball positions
from the tool_results claims (selected current + strip slots).
"""
import json
import sys
from collections import Counter
from pathlib import Path

import cv2
import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import vision as V  # noqa: E402

FOLDER = ROOT / ("screenshots for traning on when balls comes and the "
                 "diff diff drid numner and ball numbers")
OUT = ROOT / "debug" / "eval" / "ballpick"
OUT.mkdir(parents=True, exist_ok=True)

FILES = sorted(FOLDER.glob("*.png"))
NAME = {f"e{i:02d}": p for i, p in enumerate(FILES)}
TRUTH = json.load(open(ROOT / "debug" / "eval" / "truth.json",
                       encoding="utf8"))

# boxes I verified visually as phantoms (montage evidence)
PHANTOM_BOXES = {
    ("e01", 795, 216, 124), ("e09", 800, 216, 124), ("e15", 795, 216, 124),
    ("e06", 753, 233, 104), ("e22", 804, 225, 106), ("e25", 750, 231, 116),
    ("e26", 752, 224, 122), ("e05", 767, 248, 94), ("e11", 944, 296, 94),
    ("e27", 977, 273, 96), ("e17", 1097, 289, 50), ("e23", 733, 271, 58),
    ("e12", 759, 232, 112), ("e14", 754, 231, 106), ("e21", 734, 233, 128),
}
# boxes I verified visually as real balls
REAL_BOXES = {
    ("e00", 1056, 244, 94), ("e01", 1056, 243, 94), ("e02", 1066, 253, 74),
    ("e03", 1068, 255, 70), ("e05", 1068, 255, 70), ("e06", 1068, 255, 70),
    ("e07", 1068, 255, 70), ("e09", 1070, 257, 66), ("e10", 1068, 255, 70),
    ("e12", 1068, 255, 70), ("e13", 1055, 242, 96), ("e16", 1055, 242, 96),
    ("e18", 1056, 243, 94), ("e20", 1056, 244, 94), ("e21", 1055, 242, 96),
    ("e22", 1054, 243, 100), ("e23", 1055, 242, 96), ("e24", 909, 234, 110),
    ("e25", 1046, 248, 92), ("e26", 1014, 262, 80), ("e27", 1068, 255, 70),
    ("e00", 827, 282, 60), ("e00", 903, 290, 42), ("e00", 961, 282, 60),
    ("e14", 835, 289, 44), ("e14", 903, 290, 42), ("e14", 961, 282, 60),
    ("e26", 762, 283, 56), ("e26", 871, 289, 44), ("e26", 931, 282, 60),
    ("e27", 760, 282, 60), ("e27", 835, 289, 44), ("e27", 894, 281, 60),
    ("e27", 962, 282, 62),
}


def face_probe(crop):
    """Pale round face inside the crop (extractor's locator). -> (ok, frac)"""
    H, W = crop.shape[:2]
    hsv = cv2.cvtColor(crop, cv2.COLOR_BGR2HSV)
    sat, val = hsv[:, :, 1], hsv[:, :, 2]
    pale = ((sat <= 110) & (val >= 180)).astype(np.uint8) * 255
    pale = cv2.morphologyEx(pale, cv2.MORPH_CLOSE, np.ones((3, 3), np.uint8))
    n, _, st, _ = cv2.connectedComponentsWithStats(pale, 8)
    total = W * H
    fx = fy = fw = fh = 0
    best = 0.14 * total
    for i in range(1, n):
        x, y, w, h, area = st[i]
        if area > best and abs(w - h) <= 0.45 * max(w, h):
            fx, fy, fw, fh = x, y, w, h
            best = area
    if fw == 0:
        return False, best / total if total else 0.0
    return True, best / total


def box_metrics(img, box):
    x, y, w, h = [int(v) for v in box]
    r = max(2.0, w / 2.0)
    crop = img[max(0, y):y + h, max(0, x):x + w]
    if crop.size == 0:
        return None
    hsv = cv2.cvtColor(crop, cv2.COLOR_BGR2HSV)
    S = hsv[:, :, 1].astype(np.float32)
    Val = hsv[:, :, 2].astype(np.float32)
    hh, ww = S.shape
    yy, xx = np.mgrid[0:hh, 0:ww]
    d2 = (xx - ww / 2.0) ** 2 + (yy - hh / 2.0) ** 2
    ring = (d2 >= (0.72 * ww / 2) ** 2) & (d2 <= (1.02 * ww / 2) ** 2)
    disk = d2 <= (0.80 * ww / 2) ** 2
    colored = (S > 90) & (Val > 70)
    ring = ring[:hh, :ww]
    disk = disk[:hh, :ww]
    ring_frac = float(colored[ring].mean()) if ring.any() else 0.0
    disk_frac = float(colored[disk].mean()) if disk.any() else 0.0
    fok, ffrac = face_probe(crop)
    return {"x": x, "y": y, "w": w, "h": h, "d": (w + h) / 2,
            "cx": x + w / 2, "cy": y + h / 2, "ring": round(ring_frac, 3),
            "disk": round(disk_frac, 3), "face": fok,
            "face_frac": round(ffrac, 3)}


def select(feats, grid, zone_frac=0.60, dmin=0.8, dmax=1.35,
           ring_t=0.35, use_ring=True, use_face=True):
    W = grid.x1 - grid.x0
    cell = W / 5.0
    zx = grid.x0 + zone_frac * W

    def band(f):
        return dmin * cell <= f["d"] <= dmax * cell

    z = [f for f in feats if f["cx"] >= zx and band(f)
         and (not use_ring or f["ring"] >= ring_t)
         and (not use_face or f["face"])]
    if z:
        return max(z, key=lambda f: f["cx"]), "zone+band+ring+face"
    z = [f for f in feats if f["cx"] >= zx and band(f)]
    if z:
        return max(z, key=lambda f: f["cx"]), "zone+band"
    z = [f for f in feats if band(f)]
    if z:
        return max(z, key=lambda f: f["cx"]), "band-only"
    if feats:
        return max(feats, key=lambda f: f["w"] * f["h"]), "old-largest"
    return None, "none"


def main():
    from rapidocr import RapidOCR
    engine = RapidOCR()
    per_variant = Counter()
    variant_files = {}
    metrics_rows = []
    sens = {}
    ring_real, ring_ph = [], []
    face_real, face_ph = [], []
    cluster_report = []
    guide_from = 0.55
    for sid in sorted(TRUTH):
        img = cv2.imread(str(NAME[sid]))
        grid = V.find_grid(img)
        t = TRUTH[sid]
        tnum = int(t["current"][1:])
        cands = V._ball_candidates(img, grid)
        feats = []
        for c in cands:
            m = box_metrics(img, c[:4])
            if m is None:
                continue
            m["sid"] = sid
            feats.append(m)
            metrics_rows.append(m)
            key = (sid, m["x"], m["y"], m["w"])
            if key in PHANTOM_BOXES:
                ring_ph.append(m["ring"])
                face_ph.append(m["face"])
            elif key in REAL_BOXES:
                ring_real.append(m["ring"])
                face_real.append(m["face"])
        # selection variants (real pipeline: detector -> metrics -> selector)
        for tag, kw in [
            ("zone+ring+face", dict(use_ring=True, use_face=True)),
            ("zone+ring", dict(use_ring=True, use_face=False)),
            ("zone+face", dict(use_ring=False, use_face=True)),
            ("zone only", dict(use_ring=False, use_face=False)),
        ]:
            pick, stage = select(feats, grid, **kw)
            bbox = None if pick is None else (pick["x"], pick["y"],
                                              pick["w"], pick["h"])
            ok = False
            if bbox is not None:
                b = V.read_ball_robust(engine, img, bbox, min_score=0.88)
                ok = (b.get("number") == tnum or
                      (b.get("number") is None and
                       pick.get("tpl") == tnum))
            per_variant[tag] += ok
            variant_files.setdefault(tag, {})[sid] = (ok, stage)
        # OLD rule for contrast
        old = max(cands, key=lambda c: (c[2] * c[3], c[0]))[:4]
        ob = V.read_ball_robust(engine, img, old, min_score=0.88)
        per_variant["OLD largest"] += ob.get("number") == tnum
        variant_files.setdefault("OLD largest", {})[sid] = (
            ob.get("number") == tnum, "old")
        # sensitivity on the full rule and the zone-only rule
        for zf in (0.50, 0.55, 0.60, 0.65):
            for label, kw in (("full", dict(use_ring=True, use_face=True)),
                              ("zone-only",
                               dict(use_ring=False, use_face=False))):
                pick, _ = select(feats, grid, zone_frac=zf, **kw)
                bbox = None if pick is None else (pick["x"], pick["y"],
                                                  pick["w"], pick["h"])
                ok = False
                if bbox is not None:
                    b = V.read_ball_robust(engine, img, bbox, min_score=0.88)
                    ok = b.get("number") == tnum
                key = f"{zf:.2f}-{label}"
                sens.setdefault(key, [0, 0])
                sens[key][0] += ok
                sens[key][1] += 1
        # duplicates by centre proximity (for the popup/strip path)
        clusters = []
        for f in sorted(feats, key=lambda f: -f["w"] * f["h"]):
            merged = None
            for cl in clusters:
                if (abs(f["cx"] - cl["cx"]) <= 0.75 * max(f["d"], cl["d"])
                        and abs(f["cy"] - cl["cy"]) <= 0.75 *
                        max(f["d"], cl["d"])):
                    merged = cl
                    break
            if merged is None:
                clusters.append(dict(f))
            else:
                if f["w"] * f["h"] > merged["w"] * merged["h"]:
                    clusters[clusters.index(merged)] = dict(f)
        cluster_report.append((sid, len(cands), len(clusters),
                               1 + len(t.get("strip") or [])))
        # QA render with guide lines
        view = img[max(0, grid.y0 - int(grid.width * 0.95)):
                   grid.y0, max(0, grid.x0 - 40):
                   min(img.shape[1], grid.x1 + 40)].copy()
        ox = max(0, grid.x0 - 40)
        oy = max(0, grid.y0 - int(grid.width * 0.95))
        zx = grid.x0 + 0.60 * (grid.x1 - grid.x0) - ox
        band = grid.width / 5.0
        cv2.line(view, (int(zx), 0), (int(zx),
                                      view.shape[0]), (0, 220, 255), 1)
        pick, stage = select(feats, grid, use_ring=True, use_face=True)
        for f in feats:
            p = (int(f["x"] - ox + f["w"] / 2), int(f["y"] - oy + f["h"] / 2))
            rad = int(f["d"] / 2)
            chosen = pick is not None and f is pick
            col = (0, 255, 0) if chosen else (0, 0, 255)
            thr = 2 if chosen else 1
            cv2.circle(view, p, rad, col, thr)
            ok_band = 0.8 * band <= f["d"] <= 1.35 * band
            if not ok_band and not chosen:
                cv2.circle(view, p, min(view.shape[0], view.shape[1]), col, 1)
        small_w = 1280 / max(1, view.shape[1])
        view = cv2.resize(view, None, fx=small_w, fy=small_w,
                          interpolation=cv2.INTER_AREA)
        cv2.imwrite(str(OUT / f"{sid}_ballpick.png"), view)

    print("=" * 74)
    print("SELECTION ACCURACY (28 files, value confirmed by production OCR)")
    print("=" * 74)
    for tag, n in per_variant.most_common():
        print(f"  {tag:<22} {n}/28")
    for tag, files in variant_files.items():
        bad = [f"{k}({v[1]})" for k, v in files.items() if not v[0]]
        if bad and tag != "zone+ring+face":
            continue
        if bad:
            print(f"  NEW-rule failures: {bad}")

    print("\nRING VALIDATOR SEPARATION (measured on visually-verified boxes)")
    print(f"  real balls   n={len(ring_real)}  ring frac: "
          f"min={min(ring_real):.2f} med={sorted(ring_real)[len(ring_real)//2]:.2f}"
          f" max={max(ring_real):.2f}")
    print(f"  phantoms     n={len(ring_ph)}  ring frac: "
          f"min={min(ring_ph):.2f} med={sorted(ring_ph)[len(ring_ph)//2]:.2f}"
          f" max={max(ring_ph):.2f}")
    print(f"  face probe: real {sum(face_real)}/{len(face_real)} ok, "
          f"phantom {sum(face_ph)}/{len(face_ph)} ok")

    print("\nZONE SENSITIVITY (full = with validators, zone-only = without)")
    for key, (ok, tot) in sorted(sens.items()):
        print(f"  {key}: {ok}/{tot}")

    print("\nCLUSTERING (popup/strip path): candidates -> clusters vs balls")
    over = under = 0
    for sid, nc, ncl, ntruth in cluster_report:
        if ncl > ntruth:
            over += ncl - ntruth
        elif ncl < ntruth:
            under += ntruth - ncl
    print(f"  total over-count {over}, under-count {under} "
          f"(clusters = physical objects)")
    (OUT / "ballpick_metrics.json").write_text(
        json.dumps({"rows": metrics_rows,
                    "variants": {k: dict(v) for k, v in
                                 variant_files.items()},
                    "sensitivity": {str(k): v for k, v in sens.items()},
                    "ring": {"real": ring_real, "phantom": ring_ph},
                    "face": {"real": face_real, "phantom": face_ph}},
                   indent=1), encoding="utf8")
    print("\nmetrics ->", OUT / "ballpick_metrics.json")
    print("gallery ->", OUT, "*.png")


if __name__ == "__main__":
    main()
