import cv2, numpy as np, os

P = r'D:\random works by gs\bingo helper\image.png'
os.makedirs('debug', exist_ok=True)

img = cv2.imread(P)
print('shape', img.shape)

hsv = cv2.cvtColor(img, cv2.COLOR_BGR2HSV)
H, S, V = hsv[:, :, 0], hsv[:, :, 1], hsv[:, :, 2]

# saturated blobs (header blocks)
mask = ((S > 110) & (V > 70)).astype(np.uint8) * 255
mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, np.ones((3, 3), np.uint8))
n, labels, stats, cent = cv2.connectedComponentsWithStats(mask, 8)
comps = []
for i in range(1, n):
    x, y, w, h, area = stats[i]
    if area > 250 and w > 20 and h > 12:
        comps.append((int(area), int(x), int(y), int(w), int(h)))
comps.sort(reverse=True)
print('\n-- saturated components (area,x,y,w,h) --')
for c in comps[:30]:
    print(c)

# horizontal line structure in grayscale
gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
bw = cv2.adaptiveThreshold(gray, 255, cv2.ADAPTIVE_THRESH_MEAN_C,
                           cv2.THRESH_BINARY_INV, 25, 10)
hk = cv2.getStructuringElement(cv2.MORPH_RECT, (40, 1))
horiz = cv2.morphologyEx(bw, cv2.MORPH_OPEN, hk, iterations=1)
vk = cv2.getStructuringElement(cv2.MORPH_RECT, (1, 40))
vert = cv2.morphologyEx(bw, cv2.MORPH_OPEN, vk, iterations=1)

cv2.imwrite('debug/mask_sat.png', mask)
cv2.imwrite('debug/bw.png', bw)
cv2.imwrite('debug/horiz.png', horiz)
cv2.imwrite('debug/vert.png', vert)

# report long horizontal runs
ys = []
for y in range(horiz.shape[0]):
    cnt = int((horiz[y] > 0).sum())
    if cnt > 250:
        ys.append((y, cnt))
print('\n-- horizontal rows with >250 px --')
print(ys[:80])
