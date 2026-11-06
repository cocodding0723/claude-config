#!/usr/bin/env python3
"""두 화면 캡처를 비교해 바뀐 영역에 빨간 상자를 그린 비교 이미지를 만든다.
사용: image_diff.py before.png after.png out.png [--threshold 24] [--min-area 400]
출력: out.png (before | after, 바뀐 곳 상자) + 표준출력에 상자 목록 JSON"""
import json, sys
import numpy as np
from PIL import Image, ImageDraw

def boxes(mask, min_area):
    h, w = mask.shape
    seen = np.zeros_like(mask, bool); out = []
    ys, xs = np.nonzero(mask)
    for y0, x0 in zip(ys, xs):
        if seen[y0, x0]: continue
        stack = [(y0, x0)]; seen[y0, x0] = True
        x1 = x2 = x0; y1 = y2 = y0; n = 0
        while stack:
            y, x = stack.pop(); n += 1
            x1, x2, y1, y2 = min(x1, x), max(x2, x), min(y1, y), max(y2, y)
            for dy, dx in ((1,0),(-1,0),(0,1),(0,-1)):
                yy, xx = y+dy, x+dx
                if 0 <= yy < h and 0 <= xx < w and mask[yy, xx] and not seen[yy, xx]:
                    seen[yy, xx] = True; stack.append((yy, xx))
        if (x2-x1+1)*(y2-y1+1) >= min_area: out.append([int(x1), int(y1), int(x2), int(y2)])
    return out

def main():
    a, b, o = sys.argv[1:4]
    th = int(sys.argv[sys.argv.index("--threshold")+1]) if "--threshold" in sys.argv else 24
    ma = int(sys.argv[sys.argv.index("--min-area")+1]) if "--min-area" in sys.argv else 400
    A = Image.open(a).convert("RGB"); B = Image.open(b).convert("RGB")
    if A.size != B.size: B = B.resize(A.size)
    s = 4  # 축소 후 비교(잡음·속도)
    small = (max(1, A.width//s), max(1, A.height//s))
    da = np.asarray(A.resize(small), dtype=np.int16); db = np.asarray(B.resize(small), dtype=np.int16)
    mask = np.abs(da - db).max(axis=2) > th
    bx = [[v*s for v in r] for r in boxes(mask, max(1, ma//(s*s)))]
    pad = 6
    out = Image.new("RGB", (A.width*2+20, A.height), "white")
    out.paste(A, (0, 0)); out.paste(B, (A.width+20, 0))
    d = ImageDraw.Draw(out)
    for x1, y1, x2, y2 in bx:
        for ox in (0, A.width+20):
            d.rectangle([x1-pad+ox, y1-pad, x2+pad+ox, y2+pad], outline=(230, 30, 30), width=5)
    out.save(o)
    print(json.dumps({"changed_regions": len(bx), "boxes": bx}, ensure_ascii=False))

if __name__ == "__main__":
    main()
