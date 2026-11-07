import cv2, numpy as np, time
from rapidocr import RapidOCR

P = r'D:\random works by gs\bingo helper\image.png'
img = cv2.imread(P)

xs = [449, 524, 596, 669, 741, 816]
ys = [262, 333, 401, 470, 539, 608]

def cell(r, c, inset=4):
    x0, x1 = xs[c] + inset, xs[c + 1] - inset
    y0, y1 = ys[r] + inset, ys[r + 1] - inset
    return img[y0:y1, x0:x1]

engine = RapidOCR()

# inspect the output object on cell (0,0) which should be "13"
crop = cell(0, 0)
big = cv2.resize(crop, None, fx=3, fy=3, interpolation=cv2.INTER_CUBIC)
t = time.perf_counter()
out = engine(big)
dt = (time.perf_counter() - t) * 1000
print('type', type(out))
print('attrs', [a for a in dir(out) if not a.startswith('_')])
print('txts', getattr(out, 'txts', None))
print('scores', getattr(out, 'scores', None))
print('boxes', getattr(out, 'boxes', None))
print(f'{dt:.1f} ms')

print('\n-- all cells, 3x upscale --')
t0 = time.perf_counter()
for r in range(5):
    row = []
    for c in range(5):
        if r == 2 and c == 2:
            row.append('FREE')
            continue
        im = cv2.resize(cell(r, c), None, fx=3, fy=3, interpolation=cv2.INTER_CUBIC)
        o = engine(im)
        txt = o.txts[0] if getattr(o, 'txts', None) else None
        sc = float(o.scores[0]) if getattr(o, 'scores', None) else 0.0
        row.append(f'{txt}({sc:.2f})')
    print(row)
print(f'total {(time.perf_counter()-t0)*1000:.0f} ms')
