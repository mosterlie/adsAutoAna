# -*- coding: utf-8 -*-
"""两张图片相似度对比（多指标 + 前景剥离）。"""
import sys
import numpy as np
import cv2
from PIL import Image

A = "/Users/gx/.workbuddy/clipboard-images/clipboard-2026-10-15T04-33-54-849Z-46e07701.jpg"
B = "/Users/gx/.workbuddy/clipboard-images/clipboard-2026-10-15T04-33-54-849Z-245a1851.jpg"
SIZE = (512, 512)


def load(p):
    im = Image.open(p).convert("RGB")
    w, h = im.size
    g = np.array(im.convert("L"))
    arr = np.array(im)
    return im, arr, g, (w, h)


def ahash(g, s=8):
    r = cv2.resize(g, (s, s), interpolation=cv2.INTER_AREA)
    return (r > r.mean()).flatten()


def dhash(g, s=8):
    r = cv2.resize(g, (s + 1, s), interpolation=cv2.INTER_AREA)
    return (r[:, 1:] > r[:, :-1]).flatten()


def phash(g, hs=32, ls=8):
    r = cv2.resize(g, (hs, hs), interpolation=cv2.INTER_AREA).astype(np.float32)
    d = cv2.dct(r)[:ls, :ls]
    med = np.median(d[1:].flatten()) if d.size > 1 else d.mean()
    return (d > med).flatten()


def hdist(a, b):
    return int(np.count_nonzero(a != b))


def ssim(a, b, C1=6.5025, C2=58.5225):
    a = a.astype(np.float64); b = b.astype(np.float64)
    k = (11, 11)
    mu1 = cv2.GaussianBlur(a, k, 1.5); mu2 = cv2.GaussianBlur(b, k, 1.5)
    s1 = cv2.GaussianBlur(a * a, k, 1.5) - mu1 * mu1
    s2 = cv2.GaussianBlur(b * b, k, 1.5) - mu2 * mu2
    s12 = cv2.GaussianBlur(a * b, k, 1.5) - mu1 * mu2
    m = ((2 * mu1 * mu2 + C1) * (2 * s12 + C2)) / ((mu1**2 + mu2**2 + C1) * (s1 + s2 + C2))
    return float(m.mean())


def fg_mask(arr):
    """白底抠图：接近白且低饱和的像素算背景。"""
    hsv = cv2.cvtColor(arr, cv2.COLOR_RGB2HSV)
    sat, val = hsv[:, :, 1], hsv[:, :, 2]
    bg = (val > 235) & (sat < 32)
    fg = (~bg).astype(np.uint8) * 255
    fg = cv2.morphologyEx(fg, cv2.MORPH_OPEN, np.ones((5, 5), np.uint8))
    fg = cv2.morphologyEx(fg, cv2.MORPH_CLOSE, np.ones((11, 11), np.uint8))
    cnts, _ = cv2.findContours(fg, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    if not cnts:
        return fg, (0, 0, 0, 0), 0.0
    c = max(cnts, key=cv2.contourArea)
    x, y, w, h = cv2.boundingRect(c)
    area = cv2.contourArea(c)
    solidity = area / (w * h) if w * h else 0.0
    keep = np.zeros_like(fg)
    cv2.drawContours(keep, [c], -1, 255, -1)
    return keep, (x, y, w, h), solidity


def hsv_hist(arr, bins=(16, 8, 8)):
    hsv = cv2.cvtColor(arr, cv2.COLOR_RGB2HSV)
    h = cv2.calcHist([hsv], [0, 1, 2], None, bins, [0, 180, 0, 256, 0, 256])
    return cv2.normalize(h, h).flatten()


def dominant(arr, k=5):
    m = arr.reshape(-1, 3).astype(np.float32)
    idx = np.random.RandomState(0).choice(len(m), size=min(20000, len(m)), replace=False)
    crit = (cv2.TERM_CRITERIA_EPS + cv2.TERM_CRITERIA_MAX_ITER, 20, 1.0)
    _, lab, cen = cv2.kmeans(m[idx], k, None, crit, 3, cv2.KMEANS_PP_CENTERS)
    cnt = np.bincount(lab.flatten(), minlength=k)
    order = np.argsort(-cnt)
    return [(tuple(int(v) for v in cen[i]), cnt[i] / cnt.sum()) for i in order]


def name_rgb(c):
    r, g, b = c
    mx, mn = max(c), min(c)
    if mx - mn < 22:
        n = "白" if mx > 225 else ("浅灰" if mx > 170 else ("中灰" if mx > 90 else "黑"))
    elif r >= g >= b:
        n = "银/白" if (mx > 190 and mx - mn < 45) else "米/橙" if g > 150 else "红棕"
    elif b >= g >= r:
        n = "蓝"
    else:
        n = "其他"
    return n


print("=" * 78)
imA, arrA, gA, szA = load(A)
imB, arrB, gB, szB = load(B)
print(f"A(图1 日本刀型)  {szA[0]}x{szA[1]}   {imA.format}")
print(f"B(图2 黑色滚花)  {szB[0]}x{szB[1]}   {imB.format}")

gAr = cv2.resize(gA, SIZE, interpolation=cv2.INTER_AREA)
gBr = cv2.resize(gB, SIZE, interpolation=cv2.INTER_AREA)
aAr = cv2.resize(arrA, SIZE, interpolation=cv2.INTER_AREA)
aBr = cv2.resize(arrB, SIZE, interpolation=cv2.INTER_AREA)

print("\n【1. 感知哈希（整图，含白底）】")
for nm, f in (("aHash", ahash), ("dHash", dhash), ("pHash", phash)):
    ha, hb = f(gA), f(gB)
    d = hdist(ha, hb)
    print(f"  {nm:6s} 汉明距离 {d:2d}/{len(ha)}   相似度 {100*(1-d/len(ha)):5.1f}%")

print("\n【2. 颜色分布】")
for nm, arr in (("A", aAr), ("B", aBr)):
    print(f"  {nm} 主色:", ", ".join(f"{name_rgb(c)} rgb{c} {p*100:.0f}%" for c, p in dominant(arr)))
hA, hB = hsv_hist(aAr), hsv_hist(aBr)
corr = cv2.compareHist(hA.reshape(-1, 1).astype(np.float32),
                       hB.reshape(-1, 1).astype(np.float32), cv2.HISTCMP_CORREL)
inter = float(np.minimum(hA, hB).sum())
print(f"  三维 HSV 直方图相关性 {corr:.3f}   交集系数 {inter:.3f}")

print("\n【3. 整体结构】")
print(f"  灰度 SSIM            {ssim(gAr, gBr):.3f}")
eA = cv2.Canny(gAr, 60, 160); eB = cv2.Canny(gBr, 60, 160)
print(f"  边缘像素占比         A {np.count_nonzero(eA)/eA.size*100:.2f}%   B {np.count_nonzero(eB)/eB.size*100:.2f}%")
print(f"  边缘图 SSIM          {ssim(eA.astype(float), eB.astype(float)):.3f}")
hA1 = cv2.calcHist([gAr], [0], None, [64], [0, 256]).astype(np.float32)
hB1 = cv2.calcHist([gBr], [0], None, [64], [0, 256]).astype(np.float32)
print(f"  灰度直方图相关性     {cv2.compareHist(hA1, hB1, cv2.HISTCMP_CORREL):.3f}")

print("\n【4. 剥离白底后：只看商品主体】")
mA, bbA, solA = fg_mask(aAr)
mB, bbB, solB = fg_mask(aBr)
print(f"  A 主体外接框 {bbA}  占图 {np.count_nonzero(mA)/mA.size*100:.1f}%  实心度 {solA:.2f}")
print(f"  B 主体外接框 {bbB}  占图 {np.count_nonzero(mB)/mB.size*100:.1f}%  实心度 {solB:.2f}")
print(f"  长宽比  A {bbA[2]/max(bbA[3],1):.2f}   B {bbB[2]/max(bbB[3],1):.2f}")
inter_m = np.count_nonzero((mA > 0) & (mB > 0))
union_m = np.count_nonzero((mA > 0) | (mB > 0))
print(f"  主体掩膜 IoU         {inter_m/max(union_m,1):.3f}")

# 主体对齐后的 SSIM
def crop_mask(arr, mask, bb, pad=6):
    x, y, w, h = bb
    x0, y0 = max(0, x - pad), max(0, y - pad)
    x1, y1 = min(arr.shape[1], x + w + pad), min(arr.shape[0], y + h + pad)
    c = arr[y0:y1, x0:x1].copy()
    m = mask[y0:y1, x0:x1]
    return c, m

cA, cmA = crop_mask(aAr, mA, bbA)
cB, cmB = crop_mask(aBr, mB, bbB)
cAr = cv2.resize(cA, (256, 256), interpolation=cv2.INTER_AREA)
cBr = cv2.resize(cB, (256, 256), interpolation=cv2.INTER_AREA)
gcA = cv2.cvtColor(cAr, cv2.COLOR_RGB2GRAY); gcB = cv2.cvtColor(cBr, cv2.COLOR_RGB2GRAY)
print(f"  主体裁剪后灰度 SSIM  {ssim(gcA, gcB):.3f}")
print(f"  主体裁剪后 pHash 相似 "
      f"{100*(1-hdist(phash(gcA), phash(gcB))/64):.1f}%")

# 主体内的颜色（排除白底）
def fg_color(arr, mask):
    px = arr[mask > 0]
    return np.median(px, axis=0) if len(px) else np.array([0, 0, 0])


print("\n【5. 主体实际颜色（已排除白底）】")
for nm, arr, m in (("A", aAr, mA), ("B", aBr, mB)):
    med = fg_color(arr, m)
    px = arr[m > 0].astype(int)
    dark = (px.max(axis=1) < 70).mean() * 100
    print(f"  {nm} 中位色 {name_rgb(tuple(med))} rgb{tuple(int(v) for v in med)}   "
          f"近黑像素占比 {dark:.0f}%   "
          f"亮度均值 {px.mean():.0f}")


def fg_hist(arr, mask, bins=(16, 8, 8)):
    hsv = cv2.cvtColor(arr, cv2.COLOR_RGB2HSV)
    h = cv2.calcHist([hsv], [0, 1, 2], mask, bins, [0, 180, 0, 256, 0, 256])
    return cv2.normalize(h, h).flatten()


hFA, hFB = fg_hist(aAr, mA), fg_hist(aBr, mB)
print(f"  主体三维 HSV 直方图相关性 "
      f"{cv2.compareHist(hFA.reshape(-1,1).astype(np.float32), hFB.reshape(-1,1).astype(np.float32), cv2.HISTCMP_CORREL):.3f}")

print("\n【6. 局部特征匹配（ORB）——判断「是不是同一个物体」】")
orb = cv2.ORB_create(nfeatures=1500, fastThreshold=12)
for nm, gimg in (("原图", None),):
    ka, da = orb.detectAndCompute(gA, None)
    kb, db = orb.detectAndCompute(gB, None)
    bf = cv2.BFMatcher(cv2.NORM_HAMMING)
    try:
        pairs = bf.knnMatch(da, db, k=2)
    except Exception:
        pairs = []
    good = [m for m, n in pairs if m.distance < 0.75 * n.distance]
    print(f"  A 特征点 {len(ka):5d}   B 特征点 {len(kb):5d}")
    print(f"  匹配对(全部) {len(pairs):5d}   通过 Lowe 比值检验的「好匹配」 {len(good)}")
    if pairs:
        print(f"  最低匹配距离 {(min(m.distance for m,_ in pairs))}  "
              f"好匹配平均距离 {np.mean([m.distance for m in good]) if good else -1:.1f}")
    # 用单应性验证：好匹配能否支撑一致的几何变换
    if len(good) >= 8:
        src = np.float32([ka[m.queryIdx].pt for m in good]).reshape(-1, 1, 2)
        dst = np.float32([kb[m.trainIdx].pt for m in good]).reshape(-1, 1, 2)
        Hm, maskh = cv2.findHomography(src, dst, cv2.RANSAC, 5.0)
        ninl = int(maskh.sum()) if maskh is not None else 0
        print(f"  RANSAC 几何一致的内点 {ninl} / {len(good)} "
              f"({ninl/len(good)*100:.0f}%)")
    else:
        print("  RANSAC：好匹配不足 8 对，无法拟合几何变换 → 不是同一物体的两个视角")

print("\n【7. 合成对照图】")
pad = 8
def wrap(img):
    return cv2.copyMakeBorder(img, pad, pad, pad, pad, cv2.BORDER_CONSTANT, value=(235, 238, 242))
line1 = np.hstack([wrap(cv2.resize(aAr, (360, 360))), wrap(cv2.resize(aBr, (360, 360)))])
mvA = cv2.cvtColor(mA, cv2.COLOR_GRAY2RGB); mvB = cv2.cvtColor(mB, cv2.COLOR_GRAY2RGB)
line2 = np.hstack([wrap(cv2.resize(mvA, (360, 360))), wrap(cv2.resize(mvB, (360, 360)))])
line3 = np.hstack([wrap(cv2.resize(cAr, (360, 360))), wrap(cv2.resize(cBr, (360, 360)))])
grid = np.vstack([line1, line2, line3])
grid = cv2.cvtColor(grid, cv2.COLOR_RGB2BGR)
out = "/Users/gx/Desktop/mypro/adsAutoAna/data/reports/image_similarity_check.png"
cv2.imwrite(out, grid)
print(f"  → {out}")
print("=" * 78)
