import cv2, numpy as np

img = cv2.imread(r'D:\random works by gs\bingo helper\image.png')
h, w = img.shape[:2]
hsv = cv2.cvtColor(img, cv2.COLOR_BGR2HSV)
sat = (((hsv[:, :, 1] > 110) & (hsv[:, :, 2] > 70)) * 255).astype(np.uint8)
n, lab, st, ce = cv2.connectedComponentsWithStats(sat, 8)
comps = []
for i in range(1, n):
    x, y, ww, hh, area = st[i]
    if area > 500 and 15 < hh < 70 and ww > 40:
        comps.append((int(x), int(y), int(ww), int(hh), int(area)))
print('header candidates:', comps)

# group by y centre
comps.sort(key=lambda c: c[1] + c[3] / 2)
groups = []
for c in comps:
    yc = c[1] + c[3] / 2
    if groups and abs(yc - groups[-1][0]) < 12:
        groups[-1][1].append(c)
    else:
        groups.append([yc, [c]])
for yc, g in groups:
    x0 = min(c[0] for c in g); x1 = max(c[0] + c[2] for c in g)
    y0 = min(c[1] for c in g); y1 = max(c[1] + c[3] for c in g)
    print(f'group yc={yc:.0f} n={len(g)} span x{x0}-{x1} ({x1-x0}) y{y0}-{y1}')

# take the group with the widest span
yc, g = max(groups, key=lambda gg: max(c[0] + c[2] for c in gg[1]) - min(c[0] for c in gg[1]))
hx0 = min(c[0] for c in g); hx1 = max(c[0] + c[2] for c in g)
hy0 = min(c[1] for c in g); hy1 = max(c[1] + c[3] for c in g)
print(f'\nchosen header: x{hx0}-{hx1} y{hy0}-{hy1}')

gry = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
# row profile just below the header
rows = []
for y in range(hy1 - 2, min(h, hy1 + 450)):
    cnt = int((gry[y, hx0:hx1] < 215).sum())
    rows.append((y, cnt))
# local maxima
peaks = []
for i in range(1, len(rows) - 1):
    y, c = rows[i]
    if c > 0.5 * (hx1 - hx0) and c >= rows[i - 1][1] and c >= rows[i + 1][1]:
        if peaks and y - peaks[-1][0] < 12:
            if c > peaks[-1][1]:
                peaks[-1] = (y, c)
        else:
            peaks.append((y, c))
print('\nrow peaks:', peaks[:20])

# column profile inside the grid band
if len(peaks) >= 2:
    gy0, gy1 = peaks[0][0], peaks[-1][0]
    cols = []
    for x in range(max(0, hx0 - 10), min(w, hx1 + 10)):
        cnt = int((gry[gy0:gy1, x] < 215).sum())
        cols.append((x, cnt))
    vpeaks = []
    for i in range(1, len(cols) - 1):
        x, c = cols[i]
        if c > 0.5 * (gy1 - gy0) and c >= cols[i - 1][1] and c >= cols[i + 1][1]:
            if vpeaks and x - vpeaks[-1][0] < 12:
                if c > vpeaks[-1][1]:
                    vpeaks[-1] = (x, c)
            else:
                vpeaks.append((x, c))
    print('col peaks:', vpeaks)
