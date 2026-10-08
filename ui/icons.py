# -*- coding: utf-8 -*-
"""Canvas 手绘线性图标。

零图片资源、随主题换色，风格统一为「线性 + 圆头」。所有图标以 (cx, cy) 为中心、
在 size×size 的方框内绘制，返回创建的 item id 列表，便于按 tag 批量删除/换色。

设计意图：FilmSheet 的界面完全不自带位图资源，用 Canvas 图元画图标
既避免引入 PNG（缩放失真、还要打包），又能和圆角控件保持同一套视觉语言。
"""

import math


def _stroke(size):
    """按图标尺寸推导线宽，保证小图标不糊、大图标不笨。"""
    return max(1.4, round(size * 0.1, 1))


def _line(canvas, pts, color, w, tags):
    return canvas.create_line(*pts, fill=color, width=w,
                              capstyle="round", joinstyle="round", tags=tags)


def _poly(canvas, pts, color, w, tags):
    return canvas.create_polygon(*pts, fill="", outline=color, width=w,
                                 joinstyle="round", tags=tags)


def draw_icon(canvas, name, cx, cy, size, color, tags=(), bg=None):
    """在 canvas 上以 (cx,cy) 为中心绘制名为 name 的图标。

    bg 仅「月亮」图标需要（用底色挖出月牙），其余可省略。
    """
    s = size / 2.0
    w = _stroke(size)
    items = []
    add = items.append

    if name == "import":
        # 向下的箭头 + 承接托盘
        add(_line(canvas, [cx, cy - s * 0.92, cx, cy + s * 0.16], color, w, tags))
        add(_line(canvas, [cx - s * 0.34, cy - s * 0.2, cx, cy + s * 0.2], color, w, tags))
        add(_line(canvas, [cx + s * 0.34, cy - s * 0.2, cx, cy + s * 0.2], color, w, tags))
        add(_line(canvas, [cx - s * 0.92, cy + s * 0.3, cx - s * 0.92, cy + s * 0.92,
                           cx + s * 0.92, cy + s * 0.92, cx + s * 0.92, cy + s * 0.3],
                  color, w, tags))

    elif name == "grid":
        # 外框 + 一格内线，表示画幅与排版
        add(_poly(canvas, [cx - s * 0.95, cy - s * 0.75, cx + s * 0.95, cy - s * 0.75,
                           cx + s * 0.95, cy + s * 0.75, cx - s * 0.95, cy + s * 0.75],
                  color, w, tags))
        add(_line(canvas, [cx - s * 0.05, cy - s * 0.75, cx - s * 0.05, cy + s * 0.75],
                  color, w, tags))
        add(_line(canvas, [cx - s * 0.95, cy, cx + s * 0.95, cy], color, w, tags))

    elif name == "palette":
        # 调色盘：外圆 + 三个色点
        add(canvas.create_oval(cx - s * 0.95, cy - s * 0.95, cx + s * 0.95, cy + s * 0.95,
                               outline=color, width=w, tags=tags))
        for dx, dy in ((-0.36, -0.34), (0.34, -0.2), (-0.12, 0.42)):
            r = s * 0.15
            add(canvas.create_oval(cx + dx * s - r, cy + dy * s - r,
                                   cx + dx * s + r, cy + dy * s + r,
                                   outline=color, fill=color, width=0, tags=tags))

    elif name == "camera":
        # 机身 + 镜头 + 顶部取景凸起
        add(_poly(canvas, [cx - s * 0.95, cy - s * 0.5, cx - s * 0.4, cy - s * 0.5,
                           cx - s * 0.24, cy - s * 0.78, cx + s * 0.3, cy - s * 0.78,
                           cx + s * 0.46, cy - s * 0.5, cx + s * 0.95, cy - s * 0.5,
                           cx + s * 0.95, cy + s * 0.72, cx - s * 0.95, cy + s * 0.72],
                  color, w, tags))
        add(canvas.create_oval(cx - s * 0.34, cy - s * 0.14, cx + s * 0.34, cy + s * 0.54,
                               outline=color, width=w, tags=tags))

    elif name == "folder":
        add(_poly(canvas, [cx - s * 0.95, cy + s * 0.7, cx + s * 0.95, cy + s * 0.7,
                           cx + s * 0.95, cy - s * 0.35, cx + s * 0.12, cy - s * 0.35,
                           cx - s * 0.12, cy - s * 0.65, cx - s * 0.95, cy - s * 0.65],
                  color, w, tags))

    elif name == "play":
        add(canvas.create_polygon(
            cx - s * 0.5, cy - s * 0.78, cx + s * 0.82, cy, cx - s * 0.5, cy + s * 0.78,
            fill=color, outline="", joinstyle="round", tags=tags))

    elif name == "x":
        add(_line(canvas, [cx - s * 0.7, cy - s * 0.7, cx + s * 0.7, cy + s * 0.7],
                  color, w, tags))
        add(_line(canvas, [cx + s * 0.7, cy - s * 0.7, cx - s * 0.7, cy + s * 0.7],
                  color, w, tags))

    elif name == "eye":
        # 上下两道眼睑弧 + 瞳孔
        add(canvas.create_arc(cx - s, cy - s * 0.86, cx + s, cy + s * 0.86,
                              start=22, extent=136, style="arc",
                              outline=color, width=w, tags=tags))
        add(canvas.create_arc(cx - s, cy - s * 0.86, cx + s, cy + s * 0.86,
                              start=202, extent=136, style="arc",
                              outline=color, width=w, tags=tags))
        add(canvas.create_oval(cx - s * 0.3, cy - s * 0.3, cx + s * 0.3, cy + s * 0.3,
                               outline=color, width=w, tags=tags))

    elif name == "sun":
        r = s * 0.42
        add(canvas.create_oval(cx - r, cy - r, cx + r, cy + r,
                               outline=color, width=w, tags=tags))
        for i in range(8):
            a = math.radians(i * 45)
            add(_line(canvas, [cx + math.cos(a) * s * 0.66, cy + math.sin(a) * s * 0.66,
                               cx + math.cos(a) * s * 0.95, cy + math.sin(a) * s * 0.95],
                      color, w, tags))

    elif name == "moon":
        # 实心圆 + 用底色挖掉一个偏移圆 → 月牙
        add(canvas.create_oval(cx - s * 0.85, cy - s * 0.85, cx + s * 0.85, cy + s * 0.85,
                               fill=color, outline="", tags=tags))
        if bg:
            add(canvas.create_oval(cx - s * 0.15, cy - s * 1.05, cx + s * 1.5, cy + s * 0.6,
                                   fill=bg, outline="", tags=tags))

    elif name == "archive":
        # 归档盒：盒盖 + 盒体 + 标签
        add(_line(canvas, [cx - s * 0.92, cy - s * 0.52, cx + s * 0.92, cy - s * 0.52],
                  color, w, tags))
        add(_poly(canvas, [cx - s * 0.78, cy - s * 0.2, cx + s * 0.78, cy - s * 0.2,
                           cx + s * 0.78, cy + s * 0.82, cx - s * 0.78, cy + s * 0.82],
                  color, w, tags))
        add(_line(canvas, [cx - s * 0.28, cy + s * 0.2, cx + s * 0.28, cy + s * 0.2],
                  color, w, tags))

    elif name == "search":
        # 放大镜：镜圈 + 斜柄
        r = s * 0.62
        add(canvas.create_oval(cx - s * 0.42 - r, cy - s * 0.42 - r,
                               cx - s * 0.42 + r, cy - s * 0.42 + r,
                               outline=color, width=w, tags=tags))
        add(_line(canvas, [cx + s * 0.02, cy + s * 0.02, cx + s * 0.82, cy + s * 0.82],
                  color, w, tags))

    elif name == "trash":
        # 垃圾桶：盖 + 桶身 + 竖线
        add(_line(canvas, [cx - s * 0.8, cy - s * 0.52, cx + s * 0.8, cy - s * 0.52],
                  color, w, tags))
        add(_poly(canvas, [cx - s * 0.6, cy - s * 0.28, cx + s * 0.6, cy - s * 0.28,
                           cx + s * 0.46, cy + s * 0.86, cx - s * 0.46, cy + s * 0.86],
                  color, w, tags))
        for dx in (-0.2, 0.2):
            add(_line(canvas, [cx + dx * s, cy - s * 0.06, cx + dx * s, cy + s * 0.6],
                      color, w, tags))

    return items
