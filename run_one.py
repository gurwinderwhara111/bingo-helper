"""Offline tester: run the vision core on any screenshot.

Usage:
    python run_one.py "Screenshot 2026-11-06 095328.png"

This does NOT capture the screen, does NOT draw an overlay, and does NOT touch
the game. It reads one PNG you point it at, prints what it sees, and writes
annotated debug images so you can check it with your own eyes.
"""
from __future__ import annotations

import logging
import sys
import time
from pathlib import Path

import cv2
import numpy as np

import vision as V

logging.disable(logging.INFO)


def main() -> None:
    if len(sys.argv) < 2:
        print('usage: python run_one.py <screenshot.png>')
        raise SystemExit(2)

    path = sys.argv[1]
    img = cv2.imread(path)
    if img is None:
        raise SystemExit(f'cannot read {path}')
    print(f'image: {path}  {img.shape[1]}x{img.shape[0]}')

    from rapidocr import RapidOCR

    t0 = time.perf_counter()
    engine = RapidOCR()
    engine(np.full((64, 64, 3), 255, np.uint8), use_det=False, use_cls=False, use_rec=True)
    print(f'engine init: {(time.perf_counter() - t0) * 1000:.0f} ms\n')

    t = time.perf_counter()
    grid = V.find_grid(img)
    t_grid = (time.perf_counter() - t) * 1000
    if grid is None:
        raise SystemExit('GRID NOT FOUND')
    print(f'grid: ({grid.x0},{grid.y0})-({grid.x1},{grid.y1})  '
          f'{grid.width}x{grid.height}  [{t_grid:.0f} ms]')

    warped = V.rectify(img, grid)

    t = time.perf_counter()
    values, scores = V.read_card(engine, warped)
    t_card = (time.perf_counter() - t) * 1000

    print('\ncard:')
    for r in range(5):
        print('  ' + ' '.join(f'{values[r][c]:>3}' if values[r][c] is not None else '  -'
                              for c in range(5)))
    real_scores = [s for row in scores for s in row if s > 0]
    print(f'  read in {t_card:.0f} ms   '
          f'min score {min(real_scores) if real_scores else 0:.4f}')
    issues = V.validate_card(values)
    print(f'  constraints: {"OK" if not issues else issues}')

    t = time.perf_counter()
    bbox = V.find_ball(img, grid)
    t_bloc = (time.perf_counter() - t) * 1000
    if bbox is None:
        raise SystemExit('BALL NOT FOUND')

    t = time.perf_counter()
    ball = V.read_ball(engine, img, bbox)
    t_bread = (time.perf_counter() - t) * 1000
    print(f'\nball: {ball["letter"]} {ball["number"]}   '
          f'(letter {ball["letter_score"]:.4f}, number {ball["number_score"]:.4f})  '
          f'[{t_bloc + t_bread:.0f} ms]')

    num = ball['number']
    if num is None:
        decision = 'BALL UNREADABLE -> do nothing'
    else:
        pos = V.locate_number(values, num)
        if pos is None:
            decision = f'{num} is NOT on the card -> no highlight'
        else:
            decision = (f'{num} is on the card at row {pos[0] + 1}, '
                        f'col {pos[1] + 1} -> highlight that cell')
    print(f'decision: {decision}')

    annotated = V.annotate(img, grid, values, bbox, ball, decision)
    stem = Path(path).stem.replace(' ', '_')
    out_dir = Path('debug')
    out_dir.mkdir(exist_ok=True)
    out_ann = out_dir / f'{stem}_annotated.png'
    out_rect = out_dir / f'{stem}_rectified.png'
    out_ball = out_dir / f'{stem}_ball.png'
    cv2.imwrite(str(out_ann), annotated)
    cv2.imwrite(str(out_rect), warped)
    x, y, w, h = bbox
    cv2.imwrite(str(out_ball), cv2.resize(img[y:y + h, x:x + w], None,
                                          fx=4, fy=4, interpolation=cv2.INTER_NEAREST))

    print('\nopen these to eyeball it:')
    print(f'  {out_ann}')
    print(f'  {out_rect}')
    print(f'  {out_ball}')


if __name__ == '__main__':
    main()
