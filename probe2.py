import cv2, numpy as np, time
from rapidocr import RapidOCR

P = r'D:\random works by gs\bingo helper\image.png'
img = cv2.imread(P)
xs = [449, 524, 596, 669, 741, 816]
ys = [262, 333, 401, 470, 539, 608]

def cell(r, c, inset=4):
    return img[ys[r] + inset:ys[r + 1] - inset, xs[c] + inset:xs[c + 1] - inset]

engine = RapidOCR()

# warmup
engine(np.full((64, 64, 3), 255, np.uint8), use_det=False, use_cls=False, use_rec=True)

print('-- recognition-only, one cell --')
im = cell(0, 0)
for i in range(3):
    t = time.perf_counter()
    o = engine(im, use_det=False, use_cls=False, use_rec=True)
    print(f'{(time.perf_counter()-t)*1000:.1f} ms  txt={getattr(o,"txts",None)} score={getattr(o,"scores",None)}')

print('\n-- recognition-only, all 25 --')
t0 = time.perf_counter()
bad = 0
for r in range(5):
    row = []
    for c in range(5):
        if (r, c) == (2, 2):
            row.append('FREE'); continue
        o = engine(cell(r, c), use_det=False, use_cls=False, use_rec=True)
        txt = o.txts[0] if getattr(o, 'txts', None) else None
        row.append(txt)
    print(row)
print(f'total {(time.perf_counter()-t0)*1000:.0f} ms')

# ball
ball = img[125:225, 710:815]
cv2.imwrite('debug/ball_raw.png', cv2.resize(ball, None, fx=4, fy=4, interpolation=cv2.INTER_NEAREST))
inner = img[140:210, 725:800]
inner_big = cv2.resize(inner, None, fx=4, fy=4, interpolation=cv2.INTER_CUBIC)
cv2.imwrite('debug/ball_inner.png', inner_big)
o = engine(inner_big, use_det=False, use_cls=False, use_rec=True)
print('\nball inner ->', getattr(o, 'txts', None), getattr(o, 'scores', None))
