#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""生成 PWA 图标（192 / 512 maskable / 180 apple-touch）。一次性脚本，产物入库、脚本可删。"""
import os

from PIL import Image, ImageDraw

OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "docs")
BLUE = "#3d57e5"
DEEP = "#2f47c8"
WHITE = "#ffffff"
GOLD = "#ffd166"


def draw(size: int, *, pad: float = 0.12) -> Image.Image:
    """画一枚「上升折线 + 落点」的图标；pad 控制安全边距（maskable 需要 ≥10%）。"""
    S = size * 4                      # 4x 超采样再缩小，边缘平滑
    img = Image.new("RGB", (S, S), BLUE)
    d = ImageDraw.Draw(img)
    # 顶部到底部的轻微渐变（手写条纹，避免依赖 numpy）
    steps = 40
    for k in range(steps):
        t = k / (steps - 1)
        c = tuple(int(int(BLUE[j:j + 2], 16) * (1 - t) + int(DEEP[j:j + 2], 16) * t)
                  for j in (1, 3, 5))
        y0 = int(S * k / steps)
        d.rectangle([0, y0, S, int(S * (k + 1) / steps) + 1], fill=c)
    # 网格底纹
    for k in range(1, 5):
        y = S * (0.30 + 0.14 * k)
        d.line([(S * 0.10, y), (S * 0.90, y)], fill="#ffffff", width=max(1, S // 400))
    p = pad * S
    x0, x1 = p, S - p
    y0, y1 = p, S - p
    w = x1 - x0
    h = y1 - y0
    # 上升折线（4 段），全部落在安全区内
    pts = [(x0 + w * 0.02, y0 + h * 0.76),
           (x0 + w * 0.30, y0 + h * 0.55),
           (x0 + w * 0.52, y0 + h * 0.66),
           (x0 + w * 0.76, y0 + h * 0.30),
           (x0 + w * 0.97, y0 + h * 0.13)]
    d.line(pts, fill=WHITE, width=int(S * 0.055), joint="curve")
    # 折点
    for i, (x, y) in enumerate(pts):
        r = S * (0.030 if i else 0.022)
        d.ellipse([x - r, y - r, x + r, y + r], fill=WHITE)
    # 末端金色强调点
    ex, ey = pts[-1]
    r = S * 0.050
    d.ellipse([ex - r, ey - r, ex + r, ey + r], fill=GOLD)
    return img.resize((size, size), Image.LANCZOS)


def main():
    os.makedirs(OUT, exist_ok=True)
    jobs = [("icon-512.png", 512, 0.12), ("icon-192.png", 192, 0.12),
            ("icon-maskable-512.png", 512, 0.18),
            ("apple-touch-icon.png", 180, 0.10),
            ("favicon-64.png", 64, 0.06)]
    for name, size, pad in jobs:
        draw(size, pad=pad).save(os.path.join(OUT, name), optimize=True)
        print("wrote", name, size)


if __name__ == "__main__":
    main()
