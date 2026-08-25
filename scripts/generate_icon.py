"""產生可愛記事本風格的 app icon（icon.ico）。"""
from pathlib import Path

from PIL import Image, ImageDraw

SIZE = 256
OUT = Path(__file__).resolve().parent.parent / "icon.ico"


def draw_notebook(size: int) -> Image.Image:
    scale = size / 256
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)

    def s(v: float) -> float:
        return v * scale

    # 陰影
    d.rounded_rectangle(
        [s(34), s(30), s(224), s(232)], radius=s(26), fill=(0, 0, 0, 40)
    )

    # 記事本封面（粉色系）
    cover = (255, 179, 198, 255)
    cover_outline = (214, 120, 148, 255)
    d.rounded_rectangle(
        [s(26), s(20), s(216), s(222)],
        radius=s(26),
        fill=cover,
        outline=cover_outline,
        width=max(1, int(s(4))),
    )

    # 右上角折角（書籤感）
    fold = [(s(216), s(20)), (s(216), s(56)), (s(180), s(20))]
    d.polygon(fold, fill=(255, 205, 217, 255), outline=cover_outline)

    # 內頁留白區塊
    d.rounded_rectangle(
        [s(50), s(46), s(198), s(200)],
        radius=s(14),
        fill=(255, 248, 250, 255),
    )

    # 內文線條
    line_color = (255, 179, 198, 255)
    for i, y in enumerate([s(78), s(104), s(130), s(156)]):
        x2 = s(172) if i != 3 else s(140)
        d.rounded_rectangle([s(66), y, x2, y + s(10)], radius=s(5), fill=line_color)

    # 左側螺旋線圈
    ring_color = (140, 100, 110, 255)
    ring_fill = (255, 255, 255, 255)
    for cy in [s(48), s(78), s(108), s(138), s(168), s(198)]:
        d.ellipse(
            [s(16), cy - s(9), s(38), cy + s(9)],
            outline=ring_color,
            width=max(1, int(s(4))),
            fill=ring_fill,
        )

    # 可愛笑臉貼紙（星星）
    star_center = (s(178), s(176))
    star_r_outer = s(22)
    star_r_inner = s(9)
    points = []
    import math

    for i in range(10):
        angle = math.pi / 2 + i * math.pi / 5
        r = star_r_outer if i % 2 == 0 else star_r_inner
        points.append(
            (star_center[0] + r * math.cos(angle), star_center[1] - r * math.sin(angle))
        )
    d.polygon(points, fill=(255, 213, 79, 255), outline=(230, 170, 40, 255))

    return img


def main() -> None:
    sizes = [16, 24, 32, 48, 64, 128, 256]
    base = draw_notebook(256)
    imgs = [draw_notebook(sz) for sz in sizes]
    base.save(OUT, format="ICO", sizes=[(sz, sz) for sz in sizes])
    print(f"已產生：{OUT}")


if __name__ == "__main__":
    main()
