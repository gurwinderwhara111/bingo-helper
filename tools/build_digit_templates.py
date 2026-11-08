"""Build digit templates for the fast card reader.

This runs OCR once over the bundled reference cards and saves the digit masks
as a small file.  Runtime then avoids OCR entirely for normal card reads and
uses the OCR reader only as a fallback.

Run after adding a new reference card if the game changes its number font:

    python tools/build_digit_templates.py
"""
from __future__ import annotations

import argparse
import sys
from collections import Counter
from pathlib import Path

import cv2
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import vision as V
from rapidocr import RapidOCR

ROOT = Path(__file__).resolve().parent.parent
OUT_DEFAULT = ROOT / "assets" / "digit_templates.npz"


def reference_items():
    items = []

    normal = ROOT / "Screenshot 2026-11-06 095328.png"
    grid = V.find_grid(cv2.imread(str(normal)))
    if grid is not None:
        items.append((normal, grid))

    instant = ROOT / "power up types with images examples" / (
        "Power Up Instant Ball  Get 3 free balls at the same time "
        "Hint These balls will come and go quickly so be ready.png")
    # Instant help art breaks line detection; use the known grid from tests.
    items.append((instant, V.Grid(309, 225, 634, 536,
                                  [309 + int(i * (634 - 309) / 5) for i in range(6)],
                                  [225 + int(i * (536 - 225) / 5) for i in range(6)])))

    for extra in (ROOT / "test_oncard.png", ROOT / "image.png"):
        img = cv2.imread(str(extra))
        if img is None:
            continue
        grid = V.find_grid(img)
        if grid is not None:
            items.append((extra, grid))
    return items


def template_vector(mask) -> np.ndarray:
    vec = mask.astype(np.float32).ravel()
    vec -= vec.mean()
    norm = np.linalg.norm(vec)
    if norm:
        vec /= norm
    return vec


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=str(OUT_DEFAULT))
    args = ap.parse_args()

    engine = RapidOCR()
    engine(np.full((64, 64, 3), 255, np.uint8),
           use_det=False, use_cls=False, use_rec=True)

    templates: list[np.ndarray] = []
    labels: list[str] = []
    used_cards = []

    for path, grid in reference_items():
        img = cv2.imread(str(path))
        if img is None:
            print("warning: could not read", path)
            continue
        warped = V.rectify(img, grid)
        values, _scores, free = V.read_card_ex(engine, warped)
        issues = V.validate_card(values, free)
        if issues:
            print("warning: reference card rejected:", path.name, issues[:2])
            continue

        added = 0
        for r in range(5):
            for c in range(5):
                if free[r][c]:
                    continue
                value = values[r][c]
                if value is None:
                    continue
                text = str(value)
                cell = V.cell_img(warped, r, c)
                masks = V.split_digits(cell, count=len(text))
                if len(masks) != len(text):
                    continue
                for ch, mask in zip(text, masks):
                    templates.append(template_vector(mask))
                    labels.append(ch)
                    added += 1
        used_cards.append((path.name, grid.rect, added))

    if not templates:
        print("error: no digit templates collected")
        return 1

    arr = np.vstack(templates).astype(np.float32)
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    np.savez(out, templates=arr, labels=np.array(labels))

    print("used cards:")
    for name, rect, added in used_cards:
        print(f"  {name:34s} grid={rect} digit masks={added}")
    print("labels:", dict(Counter(labels)))
    print("saved:", out, f"shape={arr.shape}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
