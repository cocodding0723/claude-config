#!/usr/bin/env python3
"""모음 이미지(그리드·요약본)의 한 칸이 어느 원본 캡처인지 찾는다 — 흑백 축소본 평균 차이가 가장 작은 파일.

usage: find_capture.py <cell.png> <dir>   (cell = 모음에서 잘라 낸 한 칸)
"""
import glob, os, sys
from PIL import Image, ImageChops, ImageStat

cell = Image.open(sys.argv[1]).convert("L")
scores = []
for n in glob.glob(os.path.join(sys.argv[2], "*.png")):
    try:
        im = Image.open(n)
    except Exception:
        continue
    if im.size != (1080, 2400):
        continue
    small = im.convert("L").resize(cell.size)
    scores.append((ImageStat.Stat(ImageChops.difference(small, cell)).mean[0], n))
for s, n in sorted(scores)[:5]:
    print(f"{s:6.2f}  {n}")
