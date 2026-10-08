# -*- coding: utf-8 -*-
"""抗锯齿图形生成 —— 用 PIL 超采样换掉 Tk Canvas 的硬边光栅化。

为什么需要它
------------
Tk Canvas 没有抗锯齿 API。``create_polygon(..., smooth=True)`` 生成的是三次
贝塞尔曲线（可从 Canvas 的 PostScript 导出里直接看到 ``curveto``），但把它
光栅化到像素时**不做颜色混合** —— 边缘是硬阶梯。在 Retina 屏上 1 个逻辑像素
= 2 个物理像素，阶梯被放大，看起来就是"锯齿"。

Pillow 的 ``rounded_rectangle`` 也画整数边界，但我们可以**先在 N倍尺寸上画，
再用 LANCZOS 缩回来** —— 缩放过程天然做了边缘混合，这就是抗锯齿。

性能
----
实测（SS=4）：136x34 按钮约 1.2ms，200x40 约 1.9ms，卡片 186x200 约 11.5ms。
按钮级可以随手重画；卡片/窗口级必须缓存，所以这里带一层 LRU。

HiDPI
-----
Tk 的坐标是逻辑像素，物理像素 =逻辑 × scale。上采样倍数要同时满足两点：
够密（>=3 才能压掉阶梯）、够快（太大则耗时暴涨）。4 是当前实测的平衡点。
"""

from __future__ import annotations

import threading
from collections import OrderedDict

# 超采样倍数。3 勉强、4 干净、5+ 收益递减但耗时线性上涨。
SUPERSAMPLE = 4

# 缓存条目上限。按「控件类型 × 尺寸 × 主题」估算，200 条足够覆盖
# 一个界面上所有会同时出现的形状。
_CACHE_MAX = 240

_lock = threading.Lock()
_cache: "OrderedDict[tuple, object]" = OrderedDict()
_hits = 0
_misses = 0


def cache_stats() -> dict:
    """缓存命中统计，供测试与调优使用。"""
    with _lock:
        total = _hits + _misses
        return {
            "entries": len(_cache),
            "hits": _hits,
            "misses": _misses,
            "hit_rate": (_hits / total) if total else 0.0,
        }


def clear_cache():
    with _lock:
        _cache.clear()
        globals()["_hits"] = 0
        globals()["_misses"] = 0


# --------------------------------------------------------------------------- #
#  颜色
# --------------------------------------------------------------------------- #

def to_rgba(color, alpha: int = 255):
    """把 Tk 颜色（#rrggbb / 名字/ 元组）转成 (r,g,b,a)。

    Tk 接受 "#rrggbb"、"red"、"#abc" 这类写法，而 Pillow 只认元组或
    "#rrggbb"。这里统一处理，避免调用方各写一套转换。
    """
    if color is None:
        return None
    if isinstance(color, (tuple, list)):
        vals = list(color)
        if len(vals) >= 4:
            return tuple(int(v) for v in vals[:4])
        if len(vals) == 3:
            return (int(vals[0]), int(vals[1]), int(vals[2]), alpha)
        if len(vals) == 1:
            g = int(vals[0])
            return (g, g, g, alpha)
        return None
    s = str(color).strip()
    if not s:
        return None
    if s.startswith("#"):
        h = s[1:]
        if len(h) == 3:# #abc
            h = "".join(ch * 2 for ch in h)
        if len(h) >= 6:
            try:
                return (int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16), alpha)
            except ValueError:
                return None
        if len(h) == 2:# #ab -> 灰度
            try:
                g = int(h * 2, 16)
                return (g, g, g, alpha)
            except ValueError:
                return None
        return None
    # Tk 的标准色名。Tkinter 的 _get_color_maps 有全表，这里只列用到的。
    named = {
        "white": (255, 255, 255), "black": (0, 0, 0),
        "red": (255, 0, 0), "green": (0, 128, 0), "blue": (0, 0, 255),
        "gray": (128, 128, 128), "grey": (128, 128, 128),
        "yellow": (255, 255, 0), "orange": (255, 165, 0),
    }
    low = s.lower()
    if low in named:
        r, g, b = named[low]
        return (r, g, b, alpha)
    try:                     # Tk 的 #rrggbb 可能带空格或不同大小写
        root = color if isinstance(color, str) else s
        r, g, b = root.winfo_rgb(s)[:3]     # type: ignore[attr-defined]
        return (r, g, b, alpha)
    except Exception:
        return None


# --------------------------------------------------------------------------- #
#  圆角矩形位图
# --------------------------------------------------------------------------- #

def rounded_rect_pil(w: int, h: int, r: int, fill=None, outline=None,
                     outline_width: int = 1, ss: int = SUPERSAMPLE):
    """生成抗锯齿的圆角矩形 RGBA 图。

    Args:
        w, h: 逻辑尺寸（像素）
        r:圆角半径（逻辑像素）
        fill: 填充色，None/空= 不填充
        outline: 描边色，None/空 = 不描边
        outline_width: 描边宽度（逻辑像素）
        ss: 超采样倍数

    Returns:
        ``PIL.Image.Image`` (RGBA, 尺寸 w×h)。参数非法或 Pillow 不可用时返回 None。
    """
    if w <= 0 or h <= 0:
        return None
    try:
        from PIL import Image, ImageDraw
    except ImportError:
        return None

    r = max(0, min(int(r), w // 2, h // 2))
    ow = max(0, int(outline_width))
    f = to_rgba(fill) if fill else None
    o = to_rgba(outline) if outline else None
    if f is None and o is None:
        return None
    # 描边画在轮廓内侧（和 Tk 的 width=1 一致）
    inset = (ow - 1) // 2 if ow else 0
    ss = max(1, min(8, int(ss)))

    W, H = max(1, w * ss), max(1, h * ss)
    im = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    d = ImageDraw.Draw(im)

    box = [inset * ss, inset * ss, W - 1 - inset * ss, H - 1 - inset * ss]
    if box[2] <= box[0] or box[3] <= box[1]:
        return None
    # Pillow 的 radius 不能超过边长一半
    rr = min(int(r * ss), (box[2] - box[0]) // 2, (box[3] - box[1]) // 2)
    d.rounded_rectangle(box, radius=rr, fill=f,
                        outline=o, width=ow * ss if ow else 0)

    if ss == 1:
        return im
    # 关键一步：LANCZOS 缩放做边缘混合 —— 这就是抗锯齿的来源
    return im.resize((w, h), Image.LANCZOS)


def cached_rounded_rect(w: int, h: int, r: int, fill=None, outline=None,
                        outline_width: int = 1, ss: int = SUPERSAMPLE,
                        use_cache: bool = True):
    """同 :func:`rounded_rect_pil`，但带 LRU 缓存。

    返回的是**同一个 PIL Image 对象**的复用时请注意：调用方若要就地修改会
    污染缓存。本模块的使用方式都是"转PhotoImage 显示"，不修改像素，安全。
    """
    if not use_cache:
        return rounded_rect_pil(w, h, r, fill, outline, outline_width, ss)

    key = (int(w), int(h), int(r), str(fill), str(outline), int(outline_width), int(ss))
    with _lock:
        hit = _cache.get(key)
        if hit is not None:
            _cache.move_to_end(key)
            globals()["_hits"] += 1
            return hit
        globals()["_misses"] += 1

    im = rounded_rect_pil(w, h, r, fill, outline, outline_width, ss)

    with _lock:
        _cache[key] = im
        _cache.move_to_end(key)
        while len(_cache) > _CACHE_MAX:
            _cache.popitem(last=False)
    return im


# --------------------------------------------------------------------------- #
#  给 Tk 用
# --------------------------------------------------------------------------- #

class CanvasAA:
    """给 tk.Canvas 用的抗锯齿图元绘制。

    用法：把实例挂在 Canvas 上，然后 ``aa.round_rect(x, y, w, h, r, fill=...)``
    —— 内部转成 PhotoImage 贴上去。返回值可直接交给 ``canvas.delete()``。

    为什么要有这层封装：PhotoImage 必须持有引用，否则被 GC 后 Canvas 上就是
    一块空白（这是 Tk 的经典陷阱，见 ui/archive_page.py 里_photo_refs 的由来）。
    """

    def __init__(self, canvas):
        self.canvas = canvas
        self._photos = {}      # item id -> PhotoImage，必须持有引用
        self.photo = None      # 最近一次的引用

    def clear(self):
        """清空所有抗锯齿图元并释放PhotoImage 引用。"""
        for item in list(self._photos):
            try:
                self.canvas.delete(item)
            except Exception:
                pass
        self._photos.clear()
        self.photo = None

    def round_rect(self, w: int, h: int, r: int, fill=None, outline=None,
                   outline_width: int = 1, ss: int = SUPERSAMPLE,
                   tag: str = "", x: int = 0, y: int = 0):
        """在 canvas 的 (x, y) 处画一个抗锯齿圆角矩形。返回 canvas item id。

        ``tag`` 很关键：RoundedCard 重绘时只``delete("card")``，如果底图没有
        同名 tag，旧图元会留在画布上叠着 —— 尺寸变化时就会看到"残影"。
        """
        if w <= 0 or h <= 0:
            return None
        im = cached_rounded_rect(w, h, r, fill, outline, outline_width, ss)
        if im is None:
            return None
        try:
            from PIL import ImageTk
        except ImportError:
            return None
        try:
            photo = ImageTk.PhotoImage(im, master=self.canvas)
        except Exception:
            return None
        self.photo = photo
        kw = {"tags": (tag,)} if tag else {}
        item = self.canvas.create_image(x, y, image=photo, anchor="nw", **kw)
        self._photos[item] = photo
        return item

    def drop(self, item):
        """删掉单个图元并释放其 PhotoImage。"""
        photo = self._photos.pop(item, None)
        if photo is None:
            return
        try:
            self.canvas.delete(item)
        except Exception:
            pass

    def drop_tag(self, tag: str):
        """删掉某个 tag 下的所有抗锯齿图元，并清理 PhotoImage 引用。

        **必须在 ``canvas.delete(tag)`` 之前调用。**
        canvas 先删掉图元后，``find_withtag`` 就找不到那些 id 了，引用会成为
        永远清不掉的孤儿 —— 实测卡片重绘 11 次后仍残留 11 个 PhotoImage。

        顺序反了也不要紧：``prune()`` 会兜住（它按 canvas.type() 判存活）。
        """
        if not self._photos:
            return
        try:
            dead = list(self.canvas.find_withtag(tag))
        except Exception:
            dead = []
        for item in dead:
            photo = self._photos.pop(item, None)
            if photo is not None:
                try:
                    self.canvas.delete(item)
                except Exception:
                    pass
        # canvas 里可能还有不是本层创建的同 tag 图元（矢量绘制的兼容路径）
        try:
            for item in self.canvas.find_withtag(tag):
                self._photos.pop(item, None)
        except Exception:
            pass

    def prune(self):
        """丢弃已被canvas.delete('all') 删掉的图元引用。

        控件重绘通常先 ``delete("all")``，那些 item id 已失效但引用还挂在
        字典里。不清理的话 PhotoImage 会一直占着内存 —— 尤其在 hover 时
        高频重绘的场景下会明显泄漏。
        """
        if not self._photos:
            return
        alive = set()
        for item in list(self._photos):
            try:
                if self.canvas.type(item):
                    alive.add(item)
            except Exception:
                pass
        dead = [k for k in self._photos if k not in alive]
        for k in dead:
            self._photos.pop(k, None)