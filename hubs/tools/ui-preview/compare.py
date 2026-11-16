"""시안|렌더 좌우 비교 PNG. 렌더 쪽 머리글에 게임 배경 없음 표기."""
import sys

from PIL import Image, ImageDraw

HEIGHT = 1280
GAP = 20
HEADER = 40


def fit(path):
    image = Image.open(path).convert("RGB")
    return image.resize((int(image.width * HEIGHT / image.height), HEIGHT))


def main():
    if len(sys.argv) != 5:
        sys.exit("사용: compare.py <시안 PNG> <렌더 PNG> <출력 PNG> <머리글 태그>")
    design_path, render_path, out_path, tag = sys.argv[1:]
    design, render = fit(design_path), fit(render_path)
    canvas = Image.new("RGB", (design.width + render.width + GAP, HEIGHT + HEADER), "white")
    canvas.paste(design, (0, HEADER))
    canvas.paste(render, (design.width + GAP, HEADER))
    draw = ImageDraw.Draw(canvas)
    draw.text((8, 10), "design: " + design_path.rsplit("/", 1)[-1], fill="black")
    draw.text((design.width + GAP + 8, 10), f"render: {render_path.rsplit('/', 1)[-1]} @{tag} (no game background)", fill="black")
    canvas.save(out_path)
    print(out_path)


if __name__ == "__main__":
    main()
