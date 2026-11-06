#!/usr/bin/env python3
"""버튼 위치(이미지 안 비율)를 읽기 위한 확인 시트 — 화면마다 10% 격자를 그려 한 장으로 붙인다.

usage: grid.py <out.png> <img1> [img2 ...]   → Read 로 열어 버튼이 몇 % 지점에 있는지 읽는다
"""
import sys
from PIL import Image, ImageDraw

W, H = 270, 600
out, names = sys.argv[1], sys.argv[2:]
sheet = Image.new("RGB", (W * len(names), H + 16), "white")
d = ImageDraw.Draw(sheet)
for i, n in enumerate(names):
    im = Image.open(n).convert("RGB").resize((W, H))
    g = ImageDraw.Draw(im)
    for k in range(1, 10):
        g.line([(0, H * k / 10), (W, H * k / 10)], fill=(255, 0, 200))
        g.line([(W * k / 10, 0), (W * k / 10, H)], fill=(255, 0, 200))
    sheet.paste(im, (i * W, 16))
    d.text((i * W + 3, 2), n.split("/")[-1], fill="black")
sheet.save(out)
