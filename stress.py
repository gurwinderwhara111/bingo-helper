import cv2, numpy as np, time, logging
import vision as V
logging.disable(logging.INFO)
from rapidocr import RapidOCR

EXPECTED = [
    [13, 30, 33, 58, 66],
    [9, 28, 43, 50, 63],
    [1, 26, None, 59, 65],
    [12, 25, 45, 49, 64],
    [11, 19, 38, 52, 72],
]

img0 = cv2.imread(r'D:\random works by gs\bingo helper\image.png')
e = RapidOCR()
e(np.full((64, 64, 3), 255, np.uint8), use_det=False, use_cls=False, use_rec=True)

rows = []


def run(name, im):
    t = time.perf_counter()
    g = V.find_grid(im)
    if g is None:
        print(f'{name:22s} GRID NOT FOUND')
        rows.append((name, None, 0))
        return
    w = V.rectify(im, g)
    vals, _ = V.read_card(e, w)
    ok = sum(1 for r in range(5) for c in range(5)
             if not (r == 2 and c == 2) and vals[r][c] == EXPECTED[r][c])
    dt = (time.perf_counter() - t) * 1000
    print(f'{name:22s} grid OK   {ok}/24   {dt:6.0f} ms   rect=({g.x0},{g.y0})-({g.x1},{g.y1})')
    rows.append((name, g, ok))


run('original', img0)

for f in (0.6, 0.75, 1.25, 1.5):
    im = cv2.resize(img0, None, fx=f, fy=f,
                    interpolation=cv2.INTER_AREA if f < 1 else cv2.INTER_CUBIC)
    run(f'scale x{f}', im)

for b in (-30, 30):
    run(f'brightness {b:+d}', np.clip(img0.astype(np.int16) + b, 0, 255).astype(np.uint8))

for q in (50, 30):
    ok, buf = cv2.imencode('.jpg', img0, [int(cv2.IMWRITE_JPEG_QUALITY), q])
    run(f'jpeg q{q}', cv2.imdecode(buf, cv2.IMREAD_COLOR))

for s in (0.8, 1.5):
    run(f'blur sigma {s}', cv2.GaussianBlur(img0, (0, 0), s))

print('\nsummary:')
good = [r for r in rows if r[1] is not None and r[2] == 24]
print(f'  full 24/24 : {len(good)}/{len(rows)}')
print(f'  grid found : {sum(1 for r in rows if r[1] is not None)}/{len(rows)}')
