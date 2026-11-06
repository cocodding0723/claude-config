#!/usr/bin/env python3
"""보드에 올릴 화면을 만든다 — 캡처 위에 렌더(투명 PNG)를 어둡게 합성하고, 계정 식별자를 가리고, 360x800 JPEG 로 줄인다.

usage: prep_images.py <spec.json> <out_dir>
spec.json: [{"name": "boot_confirm", "src": "b16.png",
             "overlay": "boot_confirm.png", "dim": 90,          # 선택: 모달 렌더를 배경 위에(dim = 0~255 어둡게)
             "blur": [[0,150,700,330]]}]                         # 선택: 1080x2400 기준 가릴 영역(uid 등)
"""
import json, os, sys
from PIL import Image, ImageFilter

SIZE = (1080, 2400)

def load(path):
    im = Image.open(path).convert("RGBA")
    return im if im.size == SIZE else im.resize(SIZE, Image.LANCZOS)

def main(spec_path, out_dir):
    base = os.path.dirname(os.path.abspath(spec_path))
    os.makedirs(out_dir, exist_ok=True)
    total = 0
    for job in json.load(open(spec_path, encoding="utf-8")):
        im = load(os.path.join(base, job["src"]))
        if job.get("overlay"):
            im = Image.alpha_composite(im, Image.new("RGBA", SIZE, (0, 0, 0, job.get("dim", 90))))
            im = Image.alpha_composite(im, load(os.path.join(base, job["overlay"])))
        for box in job.get("blur", []):
            box = tuple(box)
            im.paste(im.crop(box).filter(ImageFilter.GaussianBlur(18)), box)
        out = os.path.join(out_dir, job["name"] + ".jpg")
        im.convert("RGB").resize((360, 800), Image.LANCZOS).save(out, quality=80, optimize=True)
        total += os.path.getsize(out)
    print(f"{out_dir}: {total // 1024} KB")

if __name__ == "__main__":
    main(*sys.argv[1:3])
