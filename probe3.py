import cv2, numpy as np, time
from rapidocr import RapidOCR

img = cv2.imread(r'D:\random works by gs\bingo helper\image.png')
engine = RapidOCR()
engine(np.full((64, 64, 3), 255, np.uint8), use_det=False, use_cls=False, use_rec=True)

# --- locate cyan ring ---
hsv = cv2.cvtColor(img, cv2.COLOR_BGR2HSV)
cyan = cv2.inRange(hsv, (85, 90, 90), (105, 255, 255))
n, lab, st, ce = cv2.connectedComponentsWithStats(cyan, 8)
cands = [(st[i, 4], *st[i, :4]) for i in range(1, n) if st[i, 4] > 800]
cands.sort(reverse=True)
print('ring candidates', cands[:5])
_, x, y, w, h = cands[0]
cx, cy, r = x + w // 2, y + h // 2, min(w, h) // 2
print('ball center', cx, cy, 'r', r)

# --- inner circle ---
inset = int(r * 0.22)
x0, x1 = cx - r + inset, cx + r - inset
y0, y1 = cy - r + inset, cy + r - inset
inner = img[y0:y1, x0:x1]
cv2.imwrite('debug/ball_inner2.png', cv2.resize(inner, None, fx=6, fy=6, interpolation=cv2.INTER_NEAREST))

# --- isolate dark glyphs, split into rows ---
g = cv2.cvtColor(inner, cv2.COLOR_BGR2GRAY)
_, th = cv2.threshold(g, 130, 255, cv2.THRESH_BINARY_INV)
n2, lab2, st2, _ = cv2.connectedComponentsWithStats(th, 8)
boxes = [tuple(st2[i, :4]) for i in range(1, n2) if st2[i, 4] > 40]
print('glyph boxes', boxes)

# cluster by vertical position
rows = []
for b in sorted(boxes, key=lambda b: b[1]):
    if rows and b[1] - rows[-1][-1][1] < 12:
        rows[-1].append(b)
    else:
        rows.append([b])
print('row clusters', rows)

def union(bs):
    xs = [b[0] for b in bs]; ys = [b[1] for b in bs]
    xe = [b[0] + b[2] for b in bs]; ye = [b[1] + b[3] for b in bs]
    return min(xs), min(ys), max(xe), max(ye)

for i, row in enumerate(rows):
    ux0, uy0, ux1, uy1 = union(row)
    pad = 6
    crop = inner[max(0, uy0 - pad):uy1 + pad, max(0, ux0 - pad):ux1 + pad]
    big = cv2.resize(crop, None, fx=6, fy=6, interpolation=cv2.INTER_CUBIC)
    cv2.imwrite(f'debug/ball_glyph_{i}.png', big)
    o = engine(big, use_det=False, use_cls=False, use_rec=True)
    print(f'row {i} boxes={row} -> txt={getattr(o,"txts",None)} score={getattr(o,"scores",None)}')

# --- timing of the whole ball path ---
t = time.perf_counter()
for _ in range(20):
    hsv2 = cv2.cvtColor(img, cv2.COLOR_BGR2HSV)
    m = cv2.inRange(hsv2, (85, 90, 90), (105, 255, 255))
ts = (time.perf_counter() - t) / 20 * 1000
print(f'ring locate: {ts:.2f} ms')
