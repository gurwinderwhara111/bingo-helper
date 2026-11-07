import cv2, time, numpy as np
from rapidocr import RapidOCR
from rapidocr.ch_ppocr_rec.typings import TextRecInput
import vision as V

img = cv2.imread(r'D:\random works by gs\bingo helper\image.png')
grid = V.find_grid(img)
warped = V.rectify(img, grid)

e = RapidOCR()
e(np.full((64, 64, 3), 255, np.uint8), use_det=False, use_cls=False, use_rec=True)

cells, keys = [], []
for r in range(5):
    for c in range(5):
        if r == 2 and c == 2:
            continue
        cells.append(V.cell_img(warped, r, c))
        keys.append((r, c))

# single, per-cell
t = time.perf_counter()
single = []
for im in cells:
    o = e(im, use_det=False, use_cls=False, use_rec=True)
    single.append(o.txts[0] if o.txts else None)
t_single = (time.perf_counter() - t) * 1000

# batched
for _ in range(2):
    out = e.text_rec(TextRecInput(img=cells))
t = time.perf_counter()
for _ in range(5):
    out = e.text_rec(TextRecInput(img=cells))
t_batch = (time.perf_counter() - t) / 5 * 1000

print('single ms:', round(t_single, 1))
print('batch  ms:', round(t_batch, 1))
print('batch txts:', out.txts)
print('batch scores:', [round(float(s), 4) for s in out.scores])
print('agree:', list(out.txts) == single)
print('count:', len(out.txts), len(cells))
