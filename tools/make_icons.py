#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""FilmSheet · 菲页 — 应用图标生成器。

程序化绘制「灯板上的接触印相页」主题图标（不用 AI 位图，保证每个尺寸
几何精确、边缘锐利）：

  * 暖色灯板光背景 —— 超椭圆（squircle）底板，呼应默认渲染风格「灯板正片」
  * 条后露出第二张「页」的浅色切边 —— 暗合「银盐册」多页归档的意象
  * 居中一条 135 胶片条：上/中/下三条齿孔带 + 2×3 画幅网格
  * 每格内是一幅程序绘制的暖色小景（海夕 / 城市 / 山峦 / 台地 / 剪影 / 松林）
  * 齿孔带之间印品牌橙「24 / 400」边字，呼应 App 的结构化边字功能

输出（--out 指定目录，默认桌面 filmsheet_icons）：
  icon-1024.png / icon-macos-1024.png / icon-512.png / icon-192.png
  icon-64.png / apple-touch-icon.png(180) / favicon-32x32.png
  icon.ico（16-256 多尺寸）/ icon.icns（macOS）

用法：
  python tools/make_icons.py                      # 只写默认目录
  python tools/make_icons.py --out <dir> [--also <dir> ...]
  python tools/make_icons.py --repo               # 额外写入仓库 api/ 与 assets/

依赖：Pillow、numpy（均无版本强约束，PIL>=9 即可）。
"""

import argparse
import os
import shutil
import struct
import sys
import time

import numpy as np
from PIL import Image, ImageDraw, ImageFilter, ImageFont, ImageOps

# --------------------------------------------------------------------------- #
#  常量
# --------------------------------------------------------------------------- #

S = 4                       # 超采样：在 4096 上绘制，缩到 1024 出片
BASE = 1024                 # 主图逻辑尺寸

CREAM_TOP = (255, 248, 233)     # 灯板纸面（上）
CREAM_BOT = (252, 234, 206)     # 灯板纸面（下）
GLOW_RGB = (255, 208, 138)      # 灯板暖光
STRIP_DARK = (35, 29, 22)       # 胶片条（暖黑）
STRIP_EDGE = (18, 14, 11)       # 齿孔内衬
HOLE_CREAM = (247, 236, 215)    # 齿孔（透出灯板光）
SHEET_BEHIND = (243, 226, 198)  # 第二张「页」的切边
ORANGE = (255, 140, 60)         # 品牌橙 #FF8C3C

# 胶片条布局（1024 逻辑坐标）
STRIP_X0, STRIP_X1 = 88, 936
STRIP_Y0 = 240
BAND_H = 64                      # 齿孔带高（上/中/下）
FRAME_W, FRAME_H = 236, 176
FRAME_GAP = 14
PAD_SIDE = 56
ROWS = 2
COLS = 3
STRIP_H = BAND_H * 3 + FRAME_H * 2          # 544
STRIP_R = 30                                # 条圆角
FRAME_R = 8
HOLES_PER_BAND = 7
HOLE_W, HOLE_H = 46, 32
TICK_W, TICK_H = 15, 26                     # 齿孔带里的橙色小刻度

FONT_CANDIDATES = [
    r"C:\Windows\Fonts\arialbd.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    "/System/Library/Fonts/Helvetica.ttc",
]


# --------------------------------------------------------------------------- #
#  基础工具
# --------------------------------------------------------------------------- #

def vgrad(w, h, stops):
    """垂直多段渐变。stops = [(pos0..1, (r,g,b)), ...]"""
    ys = np.linspace(0.0, 1.0, h)
    cols = np.zeros((h, 3), dtype=np.float64)
    for i in range(len(stops) - 1):
        p0, c0 = stops[i]
        p1, c1 = stops[i + 1]
        m = (ys >= p0) & (ys <= p1)
        if not m.any():
            continue
        t = (ys[m] - p0) / max(p1 - p0, 1e-9)
        cols[m] = ((1.0 - t)[:, None] * np.array(c0, dtype=np.float64)
                   + t[:, None] * np.array(c1, dtype=np.float64))
    cols[ys < stops[0][0]] = stops[0][1]
    cols[ys > stops[-1][0]] = stops[-1][1]
    arr = np.repeat(cols[:, None, :], w, axis=1)
    return Image.fromarray(np.clip(arr, 0, 255).astype(np.uint8), "RGB")


def radial_alpha(size, cx, cy, radius, power=2.0, peak=1.0):
    """径向 alpha 图（L 模式）：中心 1 向外衰减到 0。"""
    i = (np.arange(size) + 0.5) / size
    X, Y = np.meshgrid(i, i)
    d = np.sqrt((X - cx) ** 2 + (Y - cy) ** 2) / radius
    a = np.clip(1.0 - d, 0.0, 1.0) ** power * peak
    return Image.fromarray((a * 255).astype(np.uint8), "L")


def squircle_mask(size, n=5.0, soft_px=1.6):
    """Apple 风格超椭圆蒙版（float 0..1 的 L 图）。"""
    i = (np.arange(size) + 0.5) / size * 2.0 - 1.0
    X, Y = np.meshgrid(i, i)
    v = np.abs(X) ** n + np.abs(Y) ** n
    # 边界软化带：把 (1 - v) 归一化成 0..1 的 alpha
    band = n * (2.0 * soft_px / size)
    a = np.clip((1.0 - v) / max(band, 1e-9), 0.0, 1.0)
    return Image.fromarray((a * 255).astype(np.uint8), "L")


def paste_rgba(dst, src, xy):
    """带 alpha 的 paste（src 为 RGBA）。"""
    dst.alpha_composite(src, xy)


# --------------------------------------------------------------------------- #
#  六幅小景（每个返回 RGBA，尺寸即画幅大小）
# --------------------------------------------------------------------------- #

def scene_sunset_sea(w, h):
    """海上日出。"""
    img = vgrad(w, h, [(0.0, (255, 211, 160)), (0.45, (255, 158, 119)),
                       (0.72, (240, 96, 96)), (1.0, (214, 84, 96))]).convert("RGBA")
    d = ImageDraw.Draw(img, "RGBA")
    # 太阳 + 光晕
    sx, sy, r = w * 0.52, h * 0.60, h * 0.15
    glow = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    gd = ImageDraw.Draw(glow)
    gd.ellipse([sx - r * 1.9, sy - r * 1.9, sx + r * 1.9, sy + r * 1.9],
               fill=(255, 236, 200, 110))
    glow = glow.filter(ImageFilter.GaussianBlur(r * 0.55))
    img.alpha_composite(glow)
    d.ellipse([sx - r, sy - r, sx + r, sy + r], fill=(255, 240, 214, 255))
    # 海面
    sea_top = h * 0.68
    sea = vgrad(w, h, [(0.0, (92, 56, 84)), (1.0, (44, 27, 44))]).convert("RGBA")
    mask = Image.new("L", (w, h), 0)
    md = ImageDraw.Draw(mask)
    md.rectangle([0, sea_top, w, h], fill=255)
    img.paste(sea, (0, 0), mask)
    # 海面反光
    d = ImageDraw.Draw(img, "RGBA")
    for i, frac in enumerate((0.55, 0.75, 0.95)):
        y = sea_top + (h - sea_top) * frac
        ww = r * (1.9 - 0.5 * frac)
        d.rounded_rectangle([sx - ww, y, sx + ww, y + max(2, h * 0.012)],
                            radius=h * 0.008, fill=(255, 214, 170, 150))
    return img


def scene_city_dusk(w, h):
    """城市黄昏。"""
    img = vgrad(w, h, [(0.0, (106, 90, 142)), (0.55, (176, 97, 127)),
                       (1.0, (240, 149, 106))]).convert("RGBA")
    d = ImageDraw.Draw(img, "RGBA")
    # 月亮
    d.ellipse([w * 0.74, h * 0.12, w * 0.86, h * 0.30], fill=(255, 240, 214, 235))
    # 远景楼群
    d.rectangle([0, h * 0.62, w, h], fill=(58, 44, 78, 255))
    import random
    rng = random.Random(20261017)
    x = w * 0.02
    while x < w * 0.98:
        bw = w * rng.uniform(0.07, 0.13)
        bh = h * rng.uniform(0.10, 0.30)
        d.rectangle([x, h * 0.66 - bh, x + bw, h * 0.66], fill=(36, 27, 48, 255))
        # 亮灯窗
        for _ in range(int(bw * bh / (w * h) * 60)):
            wx = x + rng.uniform(2, max(3, bw - 4))
            wy = h * 0.66 - bh + rng.uniform(3, max(4, bh - 4))
            d.rectangle([wx, wy, wx + 2, wy + 2], fill=(255, 217, 160, 220))
        x += bw + w * 0.015
    # 街道暖光
    d.rectangle([0, h * 0.92, w, h], fill=(255, 190, 130, 60))
    return img


def scene_mountains(w, h):
    """层叠山峦。"""
    img = vgrad(w, h, [(0.0, (249, 207, 155)), (0.6, (238, 154, 110)),
                       (1.0, (222, 116, 96))]).convert("RGBA")
    d = ImageDraw.Draw(img, "RGBA")
    # 三条山脊，(颜色, [(x 比例, 峰高 比例), ...] x 递增)
    ridges = [
        ((154, 98, 115), [(0.0, 0.30), (0.25, 0.52), (0.50, 0.34),
                          (0.75, 0.60), (1.0, 0.40)]),
        ((102, 66, 92),  [(0.0, 0.18), (0.30, 0.38), (0.55, 0.22),
                          (0.80, 0.44), (1.0, 0.26)]),
        ((59, 39, 64),   [(0.0, 0.08), (0.35, 0.24), (0.62, 0.10),
                          (1.0, 0.22)]),
    ]
    for color, pts in ridges:
        poly = ([(0, h)]
                + [(w * fx, h * (1.0 - fh)) for fx, fh in pts]
                + [(w, h)])
        d.polygon(poly, fill=color + (255,))
    return img


def scene_mesa(w, h):
    """荒漠台地。"""
    img = vgrad(w, h, [(0.0, (255, 224, 176)), (0.6, (245, 161, 113)),
                       (1.0, (228, 126, 92))]).convert("RGBA")
    d = ImageDraw.Draw(img, "RGBA")
    d.ellipse([w * 0.12, h * 0.10, w * 0.26, h * 0.32], fill=(255, 244, 220, 240))
    # 三层台地（平顶梯形）
    layers = [((200, 107, 82), 0.58, 0.10, 0.46, 0.16),
              ((158, 74, 66), 0.72, 0.30, 0.34, 0.22),
              ((108, 51, 57), 0.88, 0.52, 0.24, 0.18)]
    for color, base, x0, tw, th in layers:
        x0w, x1w = w * x0, w * (x0 + tw)
        top, bot = h * (1.0 - base), h
        inset = tw * w * 0.10
        d.polygon([(x0w, bot), (x0w + inset, top), (x1w - inset, top), (x1w, bot)],
                  fill=color + (255,))
    return img


def scene_silhouette(w, h):
    """人像剪影 + 落日。"""
    img = vgrad(w, h, [(0.0, (255, 185, 138)), (0.45, (242, 123, 114)),
                       (1.0, (147, 73, 106))]).convert("RGBA")
    d = ImageDraw.Draw(img, "RGBA")
    sx, sy, r = w * 0.66, h * 0.30, h * 0.11
    glow = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    gd = ImageDraw.Draw(glow)
    gd.ellipse([sx - r * 2.1, sy - r * 2.1, sx + r * 2.1, sy + r * 2.1],
               fill=(255, 230, 190, 120))
    img.alpha_composite(glow.filter(ImageFilter.GaussianBlur(r * 0.6)))
    d.ellipse([sx - r, sy - r, sx + r, sy + r], fill=(255, 238, 210, 255))
    # 地面
    d.rectangle([0, h * 0.80, w, h], fill=(52, 33, 47, 255))
    # 人物剪影（头 + 肩），居中偏左
    cx = w * 0.38
    head_r = h * 0.115
    hy = h * 0.80 - h * 0.22
    d.ellipse([cx - head_r, hy - head_r, cx + head_r, hy + head_r],
              fill=(36, 23, 34, 255))
    d.polygon([(cx - head_r * 2.1, h * 0.80),
               (cx - head_r * 1.7, hy + head_r * 0.9),
               (cx + head_r * 1.7, hy + head_r * 0.9),
               (cx + head_r * 2.1, h * 0.80)],
              fill=(36, 23, 34, 255))
    return img


def scene_pines(w, h):
    """松林湖畔。"""
    img = vgrad(w, h, [(0.0, (203, 228, 220)), (0.55, (239, 224, 180)),
                       (1.0, (245, 214, 160))]).convert("RGBA")
    d = ImageDraw.Draw(img, "RGBA")
    # 远山
    d.polygon([(0, h * 0.62), (w * 0.30, h * 0.42), (w * 0.58, h * 0.62),
               (w * 0.80, h * 0.46), (w, h * 0.60), (w, h * 0.70), (0, h * 0.70)],
              fill=(127, 160, 147, 255))
    # 松树两排
    for row, (color, base, size) in enumerate((
            ((60, 97, 84), 0.70, 0.16), ((39, 64, 57), 0.78, 0.20))):
        xs = [w * f for f in (0.08, 0.26, 0.47, 0.66, 0.86)]
        for i, x in enumerate(xs):
            off = (h * 0.03) if (i + row) % 2 else 0
            th = h * size
            top = h * base - th + off
            d.polygon([(x, top), (x - th * 0.42, h * base + off),
                       (x + th * 0.42, h * base + off)], fill=color + (255,))
    # 湖面 + 倒影
    lake = vgrad(w, h, [(0.0, (134, 173, 166)), (1.0, (94, 138, 133))]).convert("RGBA")
    mask = Image.new("L", (w, h), 0)
    ImageDraw.Draw(mask).rectangle([0, h * 0.78, w, h], fill=255)
    img.paste(lake, (0, 0), mask)
    d = ImageDraw.Draw(img, "RGBA")
    for i, x in enumerate(w * f for f in (0.12, 0.34, 0.55, 0.78)):
        d.line([(x, h * 0.78), (x, h * 0.97)], fill=(230, 240, 226, 90),
               width=max(2, int(w * 0.012)))
    return img


SCENES = [scene_sunset_sea, scene_city_dusk, scene_mountains,
          scene_mesa, scene_silhouette, scene_pines]


# --------------------------------------------------------------------------- #
#  图标绘制
# --------------------------------------------------------------------------- #

def _font(px):
    for path in FONT_CANDIDATES:
        if os.path.exists(path):
            try:
                return ImageFont.truetype(path, px)
            except Exception:
                pass
    return ImageFont.load_default()


def _draw_strip_layer(w, h):
    """在 w×h（RGBA）上画整条胶片：齿孔带 + 画幅 + 边字。返回图层。"""
    layer = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    d = ImageDraw.Draw(layer, "RGBA")
    k = w / float(BASE)                       # 逻辑坐标 → 当前尺寸
    def L(v):
        return v * k

    # --- 胶片条底板 ---
    d.rounded_rectangle([L(STRIP_X0), L(STRIP_Y0), L(STRIP_X1), L(STRIP_Y0 + STRIP_H)],
                        radius=L(STRIP_R), fill=STRIP_DARK + (255,))

    # --- 2×3 画幅 ---
    for row in range(ROWS):
        for col in range(COLS):
            fx0 = STRIP_X0 + PAD_SIDE + col * (FRAME_W + FRAME_GAP)
            fy0 = STRIP_Y0 + BAND_H + row * (FRAME_H + BAND_H)
            fx1, fy1 = fx0 + FRAME_W, fy0 + FRAME_H
            scene = SCENES[row * COLS + col](int(L(FRAME_W)), int(L(FRAME_H)))
            mask = Image.new("L", scene.size, 0)
            ImageDraw.Draw(mask).rounded_rectangle(
                [0, 0, scene.size[0] - 1, scene.size[1] - 1],
                radius=L(FRAME_R), fill=255)
            layer.paste(scene, (int(L(fx0)), int(L(fy0))), mask)
            # 画幅内描边
            d.rounded_rectangle([L(fx0), L(fy0), L(fx1), L(fy1)],
                                radius=L(FRAME_R), outline=(0, 0, 0, 96),
                                width=max(1, int(L(2.5))))

    # --- 三条齿孔带 ---
    band_centers = [STRIP_Y0 + BAND_H / 2,
                    STRIP_Y0 + BAND_H + FRAME_H + BAND_H / 2,
                    STRIP_Y0 + 2 * BAND_H + 2 * FRAME_H + BAND_H / 2]
    span0, span1 = STRIP_X0 + PAD_SIDE, STRIP_X1 - PAD_SIDE
    hole_span = span1 - span0
    pitch = (hole_span - HOLE_W) / (HOLES_PER_BAND - 1) if HOLES_PER_BAND > 1 else 0
    font = _font(int(L(30)))
    for bi, cy in enumerate(band_centers):
        for i in range(HOLES_PER_BAND):
            hx = span0 + i * pitch
            hy0 = cy - HOLE_H / 2
            # 孔内衬（深色描边）+ 灯板色孔体
            d.rounded_rectangle([L(hx - 2), L(hy0 - 2), L(hx + HOLE_W + 2), L(hy0 + HOLE_H + 2)],
                                radius=L(HOLE_H / 2 + 2), fill=STRIP_EDGE + (255,))
            d.rounded_rectangle([L(hx), L(hy0), L(hx + HOLE_W), L(hy0 + HOLE_H)],
                                radius=L(HOLE_H / 2), fill=HOLE_CREAM + (255,))
        # 上/下带印橙色边字（呼应 App 的结构化边字），落在第 gi 个孔后的间隙中央
        if bi != 1 and pitch > TICK_W * 2.2:
            for gi, txt in ((1, "24"), (4, "400")):
                tx = span0 + gi * pitch + (HOLE_W + pitch) / 2.0
                d.text((L(tx), L(cy)), txt, font=font, fill=ORANGE + (255,),
                       anchor="mm")

    # --- 条面顶光（灯板光从上方渗入，向下 1.6 个齿孔带内衰减）---
    band_px = max(1, int(L(BAND_H) * 1.6))
    fade = ImageOps.invert(Image.linear_gradient("L")).resize(
        (w, band_px), Image.LANCZOS)
    fade = fade.point(lambda v: v * 26 // 255)
    top_light = Image.new("RGBA", (w, band_px), (255, 255, 255, 0))
    top_light.putalpha(fade)
    layer.alpha_composite(top_light, (0, 0))

    return layer


def render_master():
    """渲染 1024 主图（内部 4× 超采样）。"""
    W = BASE * S
    img = Image.new("RGBA", (W, W), (0, 0, 0, 0))

    # 1) 灯板纸面背景
    bg = vgrad(W, W, [(0.0, CREAM_TOP), (1.0, CREAM_BOT)]).convert("RGBA")
    img.alpha_composite(bg)

    # 2) 暖光晕（中上一盏灯）
    ga = radial_alpha(W, 0.5, 0.40, 0.52, power=2.2)
    tint = Image.new("RGBA", (W, W), GLOW_RGB + (0,))
    tint.putalpha(ga.point(lambda v: int(v * 0.50)))
    img.alpha_composite(tint)

    # 3) 四角轻压暗（避免纸面发漂）
    vig = radial_alpha(W, 0.5, 0.5, 0.78, power=2.6)
    dark = Image.new("RGBA", (W, W), (214, 186, 148, 0))
    dark.putalpha(vig.point(lambda v: int((255 - v) * 0.10)))
    img.alpha_composite(dark)

    # 4) 第二张「页」的切边 + 其投影
    sheet = Image.new("RGBA", (W, W), (0, 0, 0, 0))
    sd = ImageDraw.Draw(sheet)
    off = 20 * S
    sd.rounded_rectangle(
        [(STRIP_X0 + 6) * S + off, (STRIP_Y0 - 6) * S - off,
         (STRIP_X1 + 6) * S + off, (STRIP_Y0 + STRIP_H - 6) * S - off],
        radius=STRIP_R * S, fill=SHEET_BEHIND + (255,))
    sh = Image.new("RGBA", (W, W), (0, 0, 0, 0))
    ImageDraw.Draw(sh).rounded_rectangle(
        [(STRIP_X0 + 6) * S + off, (STRIP_Y0 - 6) * S - off + 6 * S,
         (STRIP_X1 + 6) * S + off, (STRIP_Y0 + STRIP_H - 6) * S - off + 6 * S],
        radius=STRIP_R * S, fill=(120, 90, 60, 60))
    img.alpha_composite(sh.filter(ImageFilter.GaussianBlur(10 * S)))
    img.alpha_composite(sheet)

    # 5) 胶片条投影
    sh2 = Image.new("RGBA", (W, W), (0, 0, 0, 0))
    ImageDraw.Draw(sh2).rounded_rectangle(
        [STRIP_X0 * S, STRIP_Y0 * S + 16 * S, STRIP_X1 * S, (STRIP_Y0 + STRIP_H) * S + 16 * S],
        radius=STRIP_R * S, fill=(90, 66, 44, 78))
    img.alpha_composite(sh2.filter(ImageFilter.GaussianBlur(14 * S)))

    # 6) 胶片条本体
    strip = _draw_strip_layer(W, W)
    img.alpha_composite(strip)

    # 7) 超椭圆裁切
    mask = squircle_mask(W).resize((W, W), Image.LANCZOS)
    out = Image.new("RGBA", (W, W), (0, 0, 0, 0))
    out.paste(img, (0, 0), mask)

    # 8) 缩到 1024 出片
    return out.resize((BASE, BASE), Image.LANCZOS)


# --------------------------------------------------------------------------- #
#  导出
# --------------------------------------------------------------------------- #

def build_ico(master, path):
    sizes = [16, 24, 32, 48, 64, 128, 256]
    master.save(path, format="ICO", sizes=[(s, s) for s in sizes])


def build_icns(master, path):
    """手工组装 .icns（PNG 条目，iconutil 同款布局）。"""
    entries = [("ic07", 128), ("ic08", 256), ("ic09", 512), ("ic10", 1024),
               ("ic11", 64), ("ic12", 128), ("ic13", 512), ("ic14", 1024)]
    import io
    blobs = []
    for name, px in entries:
        buf = io.BytesIO()
        master.resize((px, px), Image.LANCZOS).save(buf, format="PNG")
        blobs.append((name, buf.getvalue()))
    total = 8 + sum(8 + len(b) for _, b in blobs)
    with open(path, "wb") as f:
        f.write(b"icns")
        f.write(struct.pack(">I", total))
        for name, data in blobs:
            f.write(name.encode("ascii"))
            f.write(struct.pack(">I", 8 + len(data)))
            f.write(data)


def export_all(master, outdir):
    os.makedirs(outdir, exist_ok=True)
    jobs = [
        ("icon-1024.png", 1024),
        ("icon-macos-1024.png", 1024),
        ("icon-512.png", 512),
        ("icon-192.png", 192),
        ("icon-64.png", 64),
        ("apple-touch-icon.png", 180),
        ("favicon-32x32.png", 32),
    ]
    for name, px in jobs:
        im = master if px == BASE else master.resize((px, px), Image.LANCZOS)
        im.save(os.path.join(outdir, name))
    build_ico(master, os.path.join(outdir, "icon.ico"))
    build_icns(master, os.path.join(outdir, "icon.icns"))
    return [name for name, _ in jobs] + ["icon.ico", "icon.icns"]


# --------------------------------------------------------------------------- #
#  main
# --------------------------------------------------------------------------- #

def main():
    ap = argparse.ArgumentParser(description="FilmSheet · 菲页 图标生成器")
    ap.add_argument("--out", action="append", default=[],
                    help="输出目录（可多次传入）")
    ap.add_argument("--repo", action="store_true",
                    help="同时写入仓库 assets/ 与 api/")
    args = ap.parse_args()

    outdirs = list(args.out) or [r"C:\Users\79242\Desktop\filmsheet_icons"]
    repo_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

    if args.repo:
        outdirs.append(os.path.join(repo_root, "assets"))
        outdirs.append(os.path.join(repo_root, "api", "_icons_tmp"))

    t0 = time.time()
    master = render_master()
    print(f"master rendered in {time.time() - t0:.1f}s")

    written = []
    for d in outdirs:
        if d.endswith("_icons_tmp"):
            # 仓库 api/ 只放 PWA 需要的两个尺寸
            os.makedirs(d, exist_ok=True)
            master.resize((192, 192), Image.LANCZOS).save(
                os.path.join(d, "icon-192.png"))
            master.resize((512, 512), Image.LANCZOS).save(
                os.path.join(d, "icon-512.png"))
            written += [os.path.join(d, n) for n in ("icon-192.png", "icon-512.png")]
        else:
            files = export_all(master, d)
            written += [os.path.join(d, n) for n in files]
            print(f"[ok] {d}: {', '.join(files)}")

    if args.repo:
        # api/_icons_tmp → api/（先备份旧占位图）
        api_dir = os.path.join(repo_root, "api")
        tmp = os.path.join(api_dir, "_icons_tmp")
        for n in ("icon-192.png", "icon-512.png"):
            src = os.path.join(tmp, n)
            dst = os.path.join(api_dir, n)
            if os.path.exists(dst):
                bak = dst + ".bak"
                shutil.copy2(dst, bak)
            shutil.copy2(src, dst)
            os.remove(src)
            print(f"[ok] repo api/{n} 已更新（旧图备份为 {n}.bak）")
        os.rmdir(tmp)
        # .bak 只是本地备份，不入库
        gi = os.path.join(repo_root, ".gitignore")
        if os.path.exists(gi):
            with open(gi, "a", encoding="utf-8") as f:
                f.write("\napi/icon-192.png.bak\napi/icon-512.png.bak\n")

    print(f"done in {time.time() - t0:.1f}s, {len(written)} files")


if __name__ == "__main__":
    main()
