"""Phase 0: prove the vision core on a still screenshot. No capture, no game."""
from __future__ import annotations

import argparse
import json
import logging
import time
from pathlib import Path

import cv2
import numpy as np

import vision as V

logging.disable(logging.INFO)

EXPECTED = [
    [13, 30, 33, 58, 66],
    [9, 28, 43, 50, 63],
    [1, 26, None, 59, 65],
    [12, 25, 45, 49, 64],
    [11, 19, 38, 52, 72],
]
EXPECTED_BALL = 5


def _clean(o):
    if isinstance(o, dict):
        return {k: _clean(v) for k, v in o.items()}
    if isinstance(o, (list, tuple)):
        return [_clean(v) for v in o]
    if isinstance(o, np.integer):
        return int(o)
    if isinstance(o, np.floating):
        return float(o)
    if isinstance(o, np.ndarray):
        return o.tolist()
    return o


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("image", nargs="?", default="image.png")
    ap.add_argument("--out", default="debug")
    args = ap.parse_args()

    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    (out / "cells").mkdir(exist_ok=True)

    img = cv2.imread(args.image)
    if img is None:
        raise SystemExit(f"cannot read {args.image}")
    print(f"image: {args.image}  {img.shape[1]}x{img.shape[0]}")

    from rapidocr import RapidOCR
    t0 = time.perf_counter()
    engine = RapidOCR()
    engine(np.full((64, 64, 3), 255, np.uint8), use_det=False, use_cls=False, use_rec=True)
    print(f"engine init: {(time.perf_counter() - t0) * 1000:.0f} ms\n")

    # ---- grid
    t = time.perf_counter()
    grid = V.find_grid(img)
    t_grid = (time.perf_counter() - t) * 1000
    if grid is None:
        raise SystemExit("GRID NOT FOUND")
    print(f"grid rect : ({grid.x0},{grid.y0})-({grid.x1},{grid.y1})  {grid.width}x{grid.height}")
    print(f"grid xs   : {grid.xs}")
    print(f"grid ys   : {grid.ys}")

    # ---- rectify
    t = time.perf_counter()
    warped = V.rectify(img, grid)
    t_warp = (time.perf_counter() - t) * 1000
    cv2.imwrite(str(out / "rectified.png"), warped)

    # ---- card
    t = time.perf_counter()
    values, scores = V.read_card(engine, warped)
    t_card = (time.perf_counter() - t) * 1000

    # warm second pass: the first call carries lazy-init cost
    t = time.perf_counter()
    V.read_card(engine, warped)
    t_card_warm = (time.perf_counter() - t) * 1000

    print("\ncard:")
    for r in range(5):
        print("  " + " ".join(f"{values[r][c]:>3}" if values[r][c] is not None else "  -" for c in range(5)))

    read_ok = sum(1 for r in range(5) for c in range(5)
                  if not (r == 2 and c == 2) and values[r][c] == EXPECTED[r][c])
    print(f"  match vs expected: {read_ok}/24")
    all_scores = [scores[r][c] for r in range(5) for c in range(5) if values[r][c] is not None]
    print(f"  OCR score min/mean: {min(all_scores):.4f} / {sum(all_scores)/len(all_scores):.4f}")

    issues = V.validate_card(values)
    print(f"  constraint check: {'OK' if not issues else str(len(issues)) + ' ISSUES'}")
    for it in issues:
        print("    -", it)

    # ---- ball
    t = time.perf_counter()
    bbox = V.find_ball(img, grid)
    t_ball_loc = (time.perf_counter() - t) * 1000
    if bbox is None:
        raise SystemExit("BALL NOT FOUND")
    print(f"\nball bbox : {bbox}")

    t = time.perf_counter()
    ball = V.read_ball(engine, img, bbox)
    t_ball_read = (time.perf_counter() - t) * 1000

    btimes = []
    for _ in range(5):
        t = time.perf_counter()
        V.read_ball(engine, img, bbox)
        btimes.append((time.perf_counter() - t) * 1000)
    btimes.sort()
    t_ball_med = btimes[len(btimes) // 2]
    num = ball["number"]
    print(f"ball read : letter={ball['letter']!r} ({ball['letter_score']:.4f})  "
          f"number={num} ({ball['number_score']:.4f})")

    # ---- checks + decision
    checks = []
    if num is None:
        checks.append("number unreadable")
    else:
        derived = V.letter_for(num)
        checks.append(f"range check: {num} -> {derived} (ball letter {ball['letter']!r})")
        if ball["letter"] and derived != ball["letter"]:
            checks.append("LETTER/NUMBER CONFLICT")
        pos = V.locate_number(values, num)
        if pos is None:
            decision = f"{num} is NOT on the card -> no highlight"
        else:
            decision = f"{num} is on the card at row {pos[0]+1} col {pos[1]+1} -> highlight"
    print("checks    :")
    for c in checks:
        print("  -", c)
    print(f"\nDECISION  : {decision}")

    # ---- annotations
    annotated = V.annotate(img, grid, values, bbox, ball, decision)
    cv2.imwrite(str(out / "annotated.png"), annotated)

    # per-cell crops + glyph contact sheet
    sheet = np.full((5 * 72, 5 * 72, 3), 255, np.uint8)
    for r in range(5):
        for c in range(5):
            cell = V.cell_img(warped, r, c)
            cv2.imwrite(str(out / "cells" / f"r{r}c{c}.png"), cell)
            gm = V.glyph_mask(cell)
            if gm is not None:
                g3 = cv2.cvtColor(cv2.resize(gm, (64, 64), interpolation=cv2.INTER_NEAREST), cv2.COLOR_GRAY2BGR)
            else:
                g3 = np.full((64, 64, 3), 220, np.uint8)
            sheet[r * 72 + 4:r * 72 + 68, c * 72 + 4:c * 72 + 68] = g3
            cv2.putText(sheet, str(values[r][c] if values[r][c] is not None else "-"),
                        (c * 72 + 4, r * 72 + 70), cv2.FONT_HERSHEY_SIMPLEX, 0.35, (0, 0, 0), 1)
    cv2.imwrite(str(out / "glyphs.png"), sheet)

    # ---- per-digit templates + leave-one-out
    templates: dict[int, list] = {d: [] for d in range(10)}
    for r in range(5):
        for c in range(5):
            if (r == 2 and c == 2) or values[r][c] is None:
                continue
            s = str(values[r][c])
            masks = V.split_digits(V.cell_img(warped, r, c), len(s))
            if len(masks) != len(s):
                continue
            for ch, gm in zip(s, masks):
                templates[int(ch)].append(gm)

    loo_total = loo_ok = 0
    margins = []
    for d, masks in templates.items():
        for i, gm in enumerate(masks):
            best = {}
            for d2, masks2 in templates.items():
                for j, g2 in enumerate(masks2):
                    if d2 == d and j == i:
                        continue
                    best[d2] = max(best.get(d2, -1.0), V.ncc(gm, g2))
            if d not in best:
                continue
            ranked = sorted(best.items(), key=lambda kv: -kv[1])
            loo_total += 1
            if ranked[0][0] == d:
                loo_ok += 1
            if len(ranked) > 1:
                margins.append(ranked[0][1] - ranked[1][1])

    n_digits = sum(len(v) for v in templates.values())
    print("\nper-digit templates (leave-one-out):")
    print("  count/digit:", {d: len(v) for d, v in templates.items() if v})
    print(f"  evaluable  : {loo_total}/{n_digits}")
    print(f"  top-1 ok   : {loo_ok}/{loo_total}")
    if margins:
        print(f"  margin min/mean: {min(margins):.3f} / {sum(margins)/len(margins):.3f}")

    ball_gm = None
    if num is not None:
        x, y, w, h = bbox
        cx, cy, rr = x + w // 2, y + h // 2, min(w, h) // 2
        inset = int(rr * 0.22)
        inner = img[cy - rr + inset:cy + rr - inset, cx - rr + inset:cx + rr - inset]
        gg = cv2.cvtColor(inner, cv2.COLOR_BGR2GRAY)
        _, th = cv2.threshold(gg, 130, 255, cv2.THRESH_BINARY_INV)
        nn, _, st, _ = cv2.connectedComponentsWithStats(th, 8)
        H, W = th.shape
        boxes = [tuple(int(v) for v in st[k, :4]) for k in range(1, nn)
                 if st[k, 4] > 40 and st[k, 0] > 1 and st[k, 1] > 1
                 and st[k, 0] + st[k, 2] < W - 1 and st[k, 1] + st[k, 3] < H - 1]
        if boxes:
            lo = min(b[1] for b in boxes)
            hi = max(b[1] + b[3] for b in boxes)
            ux0 = min(b[0] for b in boxes)
            ux1 = max(b[0] + b[2] for b in boxes)
            crop = inner[max(0, lo - 6):hi + 6, max(0, ux0 - 6):ux1 + 6]
            digits = V.split_digits(crop, len(str(num)))
            if digits:
                ball_gm = digits[0]

    if ball_gm is not None and templates:
        ranked = sorted(((max(V.ncc(ball_gm, g) for g in masks), d)
                         for d, masks in templates.items() if masks), reverse=True)
        print(f"  ball digit best template: {ranked[0][1]} ({ranked[0][0]:.3f})  "
              f"top3={[(d, round(s, 3)) for s, d in ranked[:3]]}")
        on_card = any(values[r][c] == num for r in range(5) for c in range(5))
        print(f"  value {num} on card: {on_card}")
        print(f"  digit {num} appears inside card numbers: {len(templates.get(num, [])) > 0}")
        cv2.imwrite(str(out / "ball_glyph.png"),
                    cv2.cvtColor(cv2.resize(ball_gm, (128, 128), interpolation=cv2.INTER_NEAREST),
                                 cv2.COLOR_GRAY2BGR))

    # ---- timing
    total = t_grid + t_warp + t_card_warm + t_ball_loc + t_ball_med
    print("\ntiming:")
    print(f"  grid locate : {t_grid:7.1f} ms  (once per layout change)")
    print(f"  rectify     : {t_warp:7.1f} ms  (once per layout change)")
    print(f"  card read   : {t_card:7.1f} ms cold / {t_card_warm:.1f} ms warm  (once per round)")
    print(f"  ball locate : {t_ball_loc:7.1f} ms")
    print(f"  ball read   : {t_ball_med:7.1f} ms median  (per call)")
    print(f"  TOTAL warm  : {total:7.1f} ms")
    print(f"  steady state per call (ball only): {t_ball_loc + t_ball_med:7.1f} ms")

    result = {
        "image": str(args.image),
        "grid": {"rect": grid.rect, "xs": grid.xs, "ys": grid.ys},
        "card": values,
        "scores": scores,
        "expected": EXPECTED,
        "match": f"{read_ok}/24",
        "issues": issues,
        "ball": ball,
        "ball_bbox": bbox,
        "decision": decision,
        "timing_ms": {"grid": t_grid, "rectify": t_warp,
                      "card_cold": t_card, "card_warm": t_card_warm,
                      "ball_locate": t_ball_loc, "ball_read_median": t_ball_med,
                      "total_warm": total},
    }
    (out / "result.json").write_text(json.dumps(_clean(result), indent=2), encoding="utf-8")

    print("\nartefacts:")
    for p in ["rectified.png", "annotated.png", "glyphs.png", "ball_glyph.png", "result.json"]:
        if (out / p).exists():
            print("  ", out / p)


if __name__ == "__main__":
    main()
