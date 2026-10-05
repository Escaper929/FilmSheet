# -*- coding: utf-8 -*-
"""Canvas 手绘的圆角控件集（macOS 观感，零额外依赖）。

为什么手写：ttk 的 clam 主题画不出圆角，这是当前界面「像工具不像产品」的根因。
这些控件全部用 tk.Canvas 图元自绘，因此完全跟随项目现有的 THEMES 调色板，
不引入任何第三方 UI 库。

四个控件：
  RoundedCard      圆角卡片（替代 ttk.LabelFrame），内容放进 .body
  PillButton       药丸按钮（替代 ttk.Button），支持图标/禁用/悬停/按下
  SegmentedControl 分段导航（替代自绘下划线 tab），macOS 分段控件观感
  SlimScrollbar    细圆角滚动条，以 place 覆盖在滚动区之上，不需要时完全隐形
  RoundedScale     细轨道圆钮滑杆（替代 ttk.Scale），绑 IntVar/DoubleVar

所有控件都实现 `apply_theme(colors, fonts)`，由 App._apply_theme 统一驱动换肤。
"""

import tkinter as tk
from tkinter import font as tkfont


# --------------------------------------------------------------------------- #
#  绘制辅助
# --------------------------------------------------------------------------- #

def round_rect(canvas, x1, y1, x2, y2, r, **kw):
    """在 canvas 上画圆角矩形（smooth 样条近似），返回 item id。"""
    r = max(0.0, min(r, (x2 - x1) / 2.0, (y2 - y1) / 2.0))
    pts = [x1 + r, y1, x2 - r, y1, x2, y1,
           x2, y1 + r, x2, y2 - r, x2, y2,
           x2 - r, y2, x1 + r, y2, x1, y2,
           x1, y2 - r, x1, y1 + r, x1, y1]
    return canvas.create_polygon(pts, smooth=True, **kw)


def text_width(widget, font, text):
    """用真实字体量文本宽度，用于按内容定宽。"""
    return tkfont.Font(root=widget, font=font).measure(text)


class ThemedMixin:
    """统一换肤入口：子类实现 _apply()，这里负责记住调色板与字体。"""

    def apply_theme(self, colors, fonts):
        self._colors = colors
        self._fonts = dict(fonts)
        self._apply()

    def _apply(self):
        pass


# --------------------------------------------------------------------------- #
#  圆角卡片
# --------------------------------------------------------------------------- #

class RoundedCard(ThemedMixin, tk.Frame):
    """圆角卡片。

    结构：外层 Frame 用页面底色（让圆角透出来）→ Canvas 画圆角矩形
    → 内层 body（底色 = 卡片色，与矩形填充同色所以无缝）承载内容。
    调用方把子控件放进 card.body 即可。
    """

    def __init__(self, parent, colors, fonts, title="", radius=12, pad=15,
                 parent_bg=None, title_icon=None):
        self._colors = colors
        self._fonts = dict(fonts)
        self.radius = radius
        self.pad = max(pad, radius + 3)
        bg = parent_bg or colors["bg"]
        super().__init__(parent, bg=bg, highlightthickness=0, bd=0)

        self.canvas = tk.Canvas(self, bg=bg, highlightthickness=0, bd=0,
                                takefocus=0)
        self.canvas.place(x=0, y=0, relwidth=1, relheight=1)
        # Canvas 的 lower 被重载为 tag_lower，把画布压到 body 之下要走原生 Tk 命令
        self.canvas.tk.call('lower', self.canvas._w)

        # inner = 卡片表面容器；标题头与内容区都是它的子级，
        # 这样内容区可以自由使用 grid，不会和标题头的 pack 打架。
        self.inner = tk.Frame(self, bg=colors["surface"], highlightthickness=0)
        self.inner.pack(fill=tk.BOTH, expand=True, padx=self.pad, pady=self.pad)

        self._title = None
        self._head = None
        self.title_icon = title_icon
        if title:
            self._head = tk.Frame(self.inner, bg=colors["surface"], highlightthickness=0)
            self._head.pack(fill=tk.X, pady=(0, 10))
            self._head_icon = tk.Canvas(self._head, width=18, height=18,
                                        bg=colors["surface"], highlightthickness=0, bd=0)
            if title_icon:
                self._head_icon.pack(side=tk.LEFT, padx=(0, 7))
            self._title = tk.Label(self._head, text=title, bg=colors["surface"],
                                   fg=colors["fg"], font=fonts["card_title"], anchor="w")
            self._title.pack(side=tk.LEFT)

        # 内容容器：调用方把子控件放进这里
        self.body = tk.Frame(self.inner, bg=colors["surface"], highlightthickness=0)
        self.body.pack(fill=tk.BOTH, expand=True)

        self.bind("<Configure>", self._redraw)

    # -- 对外 --
    def set_title(self, text):
        if self._title:
            self._title.config(text=text)

    # -- 内部 --
    def _redraw(self, _event=None):
        from ui.icons import draw_icon
        c = self.canvas
        c.delete("card")
        w, h = self.winfo_width(), self.winfo_height()
        if w < 6 or h < 6:
            return
        round_rect(c, 0.5, 0.5, w - 0.5, h - 0.5, self.radius,
                   fill=self._colors["surface"],
                   outline=self._colors["border"], width=1, tags="card")
        c.tag_lower("card")
        if self.title_icon:
            c.delete("headicon")
            ic = self._head_icon
            ic.configure(bg=self._colors["surface"])
            ic.delete("all")
            draw_icon(ic, self.title_icon, 9, 9, 15, self._colors["fg_muted"],
                      tags=("headicon",))

    def _apply(self):
        p, f = self._colors, self._fonts
        # 卡片浮在页面上，圆角外露出的就是页面底色 —— 直接取调色板。
        # 千万别读 master：卡片的 master 是 ttk.Frame，它没有 bg 选项，
        # cget('bg') 会抛 TclError，而 _apply_theme 里那个 except 会把它
        # 静默吞掉 → 卡片永远停在旧主题（切深色后卡片还是白的）。
        bg = p["bg"]
        self.configure(bg=bg)
        self.canvas.configure(bg=bg)
        for frame in (self.inner, self._head, self.body):
            if frame is not None:
                frame.configure(bg=p["surface"])
        for child in self.body.winfo_children():
            if isinstance(child, tk.Frame):
                child.configure(bg=p["surface"])
            elif isinstance(child, tk.Label):
                child.configure(bg=p["surface"], fg=p["fg"])
        if self._title:
            self._title.configure(bg=p["surface"], fg=p["fg"],
                                  font=f["card_title"])
        hd = getattr(self, "_head_icon", None)
        if hd is not None:
            hd.configure(bg=p["surface"])
        self._redraw()


# --------------------------------------------------------------------------- #
#  药丸按钮
# --------------------------------------------------------------------------- #

def _button_palette(p, variant, state):
    """返回 (fill, fg, border, hover) —— 按变体与状态取色。"""
    if state == "disabled":
        return p["hover"] if variant != "ghost" else p["bg"], p["disabled"], "", None
    if variant == "accent":
        return p["accent"], p["accent_fg"], "", p["accent_hover"]
    if variant == "secondary":
        return p["surface"], p["fg"], p["border"], p["hover"]
    if variant == "ghost":
        return p["bg"], p["fg_muted"], "", p["hover"]
    return p["surface"], p["fg"], p["border"], p["hover"]


class PillButton(ThemedMixin, tk.Canvas):
    """Canvas 自绘药丸按钮。

    状态用 set_state("normal"/"disabled")，不用 configure(state=...)，
    避免和 tk.Canvas 自带的 state 选项（normal/disabled/hidden）语义打架。
    """

    def __init__(self, parent, colors, fonts, text="", command=None,
                 variant="secondary", icon=None, height=34, pad_x=18,
                 min_width=0, stretch=False, bg=None, on_card=False):
        self._colors = colors
        self._fonts = dict(fonts)
        self.variant = variant
        self.icon = icon
        self.text = text
        self.command = command
        self.state = tk.NORMAL
        self._hover = False
        self._pressed = False
        self._pad_x = pad_x
        self._min_width = min_width
        # 只记「角色」（卡片内 / 页面底），不记字面色 —— 字面色换肤后会过期
        self._on_card = on_card
        self._bg_literal = bg
        self._bg = bg or (colors["surface"] if on_card else colors["bg"])

        f = fonts["button"]
        need = pad_x * 2 + text_width(parent, f, text)
        if icon:
            need += 17 + (7 if text else 0)
        width = max(min_width, need)

        super().__init__(parent, width=width, height=height, bg=self._bg,
                         highlightthickness=0, bd=0, takefocus=1)
        self._h = height
        self._radius = height / 2.0
        if stretch:
            self.pack_propagate(False) if False else None

        self.bind("<Configure>", lambda e: self._redraw())
        self.bind("<Enter>", self._on_enter)
        self.bind("<Leave>", self._on_leave)
        self.bind("<ButtonPress-1>", self._on_press)
        self.bind("<ButtonRelease-1>", self._on_release)
        self.bind("<FocusIn>", lambda e: self._redraw())
        self.bind("<FocusOut>", lambda e: self._redraw())
        self.bind("<Return>", lambda e: self.invoke())
        self.bind("<space>", lambda e: self.invoke())

    # -- 对外 API（保持与 ttk 调用点兼容） --
    def set_state(self, state):
        self.state = tk.NORMAL if state in (tk.NORMAL, "normal") else tk.DISABLED
        self.configure(cursor="" if self.state == tk.NORMAL else "")
        self._redraw()

    def set_text(self, text):
        self.text = text
        self._redraw()

    def set_command(self, command):
        self.command = command

    def invoke(self):
        if self.state == tk.NORMAL and self.command:
            self.command()

    # -- 事件 --
    def _on_enter(self, _e):
        self._hover = True
        self._redraw()

    def _on_leave(self, _e):
        self._hover = False
        self._pressed = False
        self._redraw()

    def _on_press(self, _e):
        if self.state == tk.NORMAL:
            self._pressed = True
            self._redraw()

    def _on_release(self, e):
        was = self._pressed
        self._pressed = False
        self._redraw()
        if was and 0 <= e.x <= self.winfo_width() and 0 <= e.y <= self.winfo_height():
            self.invoke()

    # -- 绘制 --
    def _redraw(self):
        from ui.icons import draw_icon
        c = self
        c.delete("all")
        w = max(c.winfo_width(), int(c.cget("width")))
        h = self._h
        fill, fg, border, hover = _button_palette(self._colors, self.variant, self.state)

        if self.state == tk.NORMAL and self._hover and hover:
            fill = hover
        if self._pressed and self.state == tk.NORMAL:
            fill = self._colors["active"] if self.variant == "secondary" else fill
            if self.variant == "accent":
                fill = self._colors["accent_active"]

        round_rect(c, 0.5, 0.5, w - 0.5, h - 0.5, self._radius,
                   fill=fill, outline=border or fill, width=1 if border else 0)

        if self.focus_get() is self:
            round_rect(c, 2.0, 2.0, w - 2.0, h - 2.0, self._radius - 1.5,
                       fill="", outline=self._colors["accent"], width=1)

        f = self._fonts["button"]
        tw = text_width(c, f, self.text) if self.text else 0
        icon_w = 17 if self.icon else 0
        gap = 7 if (self.icon and self.text) else 0
        total = icon_w + gap + tw
        x = (w - total) / 2.0
        cy = h / 2.0
        if self.icon:
            draw_icon(c, self.icon, x + icon_w / 2.0, cy, 16, fg, bg=fill)
            x += icon_w + gap
        if self.text:
            c.create_text(x, cy + 0.5, text=self.text, fill=fg, font=f, anchor="w")

    def _apply(self):
        self._bg = self._bg_literal or (self._colors["surface"] if self._on_card
                                        else self._colors["bg"])
        self.configure(bg=self._bg)
        self._redraw()


# --------------------------------------------------------------------------- #
#  分段导航
# --------------------------------------------------------------------------- #

class SegmentedControl(ThemedMixin, tk.Canvas):
    """macOS 风格分段控件：灰底轨道 + 选中项白/灰药丸。"""

    def __init__(self, parent, colors, fonts, items, command=None, height=36,
                 pad=3, bg=None):
        self._colors = colors
        self._fonts = dict(fonts)
        self.items = list(items)          # [(label, icon_name), ...]
        self.command = command
        self.index = 0
        self._hover = -1
        self._h = height
        self._pad = pad
        self._bg = bg or colors["bg"]
        super().__init__(parent, height=height, bg=self._bg,
                         highlightthickness=0, bd=0, takefocus=0)
        self.bind("<Configure>", lambda e: self._redraw())
        self.bind("<Button-1>", self._on_click)
        self.bind("<Motion>", self._on_motion)
        self.bind("<Leave>", lambda e: self._set_hover(-1))

    def select(self, index, notify=False):
        if not (0 <= index < len(self.items)) or index == self.index:
            if index == self.index:
                return
            return
        self.index = index
        self._redraw()
        if notify and self.command:
            self.command(index)

    def set_index(self, index):
        self.index = index
        self._redraw()

    def _segment_at(self, x):
        n = len(self.items)
        if n == 0:
            return -1
        w = self.winfo_width() - self._pad * 2
        seg = w / float(n)
        i = int((x - self._pad) // seg)
        return i if 0 <= i < n else -1

    def _on_click(self, e):
        i = self._segment_at(e.x)
        if i < 0 or i == self.index:
            return
        self.index = i
        self._redraw()
        if self.command:
            self.command(i)

    def _on_motion(self, e):
        self._set_hover(self._segment_at(e.x))

    def _set_hover(self, i):
        if i != self._hover:
            self._hover = i
            self._redraw()

    def _redraw(self):
        from ui.icons import draw_icon
        c = self
        c.delete("all")
        w = c.winfo_width()
        if w < 10:
            return
        p, pad, h = self._colors, self._pad, self._h
        round_rect(c, 0, 0, w, h, h / 2.0, fill=p["seg_track"],
                   outline=p["seg_track"], width=0)

        n = len(self.items)
        if n == 0:
            return
        seg = (w - pad * 2) / float(n)

        # 选中项药丸
        x0 = pad + self.index * seg
        round_rect(c, x0 + 1.5, 2.5, x0 + seg - 1.5, h - 2.5, (h - 5) / 2.0,
                   fill=p["seg_thumb"], outline=p["seg_thumb"], width=0)

        fr = self._fonts
        for i, item in enumerate(self.items):
            label, icon = item if isinstance(item, (tuple, list)) else (item, None)
            cx = pad + i * seg + seg / 2.0
            if i == self.index:
                fg, f = p["fg"], fr["nav_active"]
            elif i == self._hover:
                fg, f = p["fg"], fr["nav"]
            else:
                fg, f = p["fg_muted"], fr["nav"]
            tw = text_width(c, f, label)
            iw = 15 if icon else 0
            gap = 6 if (icon and label) else 0
            total = iw + gap + tw
            x = cx - total / 2.0
            if icon:
                draw_icon(c, icon, x + iw / 2.0, h / 2.0, 14, fg)
                x += iw + gap
            c.create_text(x, h / 2.0 + 0.5, text=label, fill=fg, font=f, anchor="w")

    def _apply(self):
        self._bg = self._colors["bg"]
        self.configure(bg=self._bg)
        self._redraw()


# --------------------------------------------------------------------------- #
#  细圆角滚动条（覆盖式，不需要时完全隐形）
# --------------------------------------------------------------------------- #

class SlimScrollbar(ThemedMixin, tk.Canvas):
    """覆盖在滚动区右上角的细圆角滚动条。

    API 兼容 ttk.Scrollbar 的 set(first, last)；不占据布局空间（用 place 覆盖）。
    内容不足以滚动时什么都不画，因此不会像 ttk.Scrollbar 那样常驻一条槽。
    """

    def __init__(self, parent, colors, command=None, width=11, thickness=7,
                 bg=None):
        self._colors = colors
        self.command = command
        # 注意：不能叫 self._w —— tk.Misc 用 _w 存控件的 Tcl 路径名，会被 super() 覆盖
        self._track_w = width
        self._thumb_t = thickness
        self._bg = bg or colors["bg"]
        self._first = 0.0
        self._last = 1.0
        self._drag = None
        super().__init__(parent, width=width, bg=self._bg,
                         highlightthickness=0, bd=0)
        self.bind("<Configure>", lambda e: self._redraw())
        self.bind("<Enter>", lambda e: self._redraw(True))
        self.bind("<Leave>", lambda e: self._redraw(False))
        self.bind("<ButtonPress-1>", self._on_press)
        self.bind("<B1-Motion>", self._on_drag)
        self.bind("<ButtonRelease-1>", lambda e: setattr(self, "_drag", None))
        self._active = False

    # -- ttk 兼容 --
    def set(self, first, last):
        try:
            self._first, self._last = float(first), float(last)
        except (TypeError, ValueError):
            return
        self._redraw()

    def _thumb_geom(self):
        h = self.winfo_height()
        span = max(0.02, self._last - self._first)
        tl = max(26.0, h * span)
        ty = (h - tl) * (self._first / max(1e-6, 1.0 - span))
        return ty, tl

    def _on_press(self, e):
        if not self.command:
            return
        ty, tl = self._thumb_geom()
        if ty <= e.y <= ty + tl:
            self._drag = e.y - ty
        else:
            span = max(0.02, self._last - self._first)
            frac = max(0.0, min(1.0 - span, (e.y - tl / 2.0) / max(1, self.winfo_height())))
            self.command("moveto", frac)

    def _on_drag(self, e):
        if self._drag is None or not self.command:
            return
        h = max(1, self.winfo_height())
        span = max(0.02, self._last - self._first)
        frac = (e.y - self._drag) / h
        self.command("moveto", max(0.0, min(1.0 - span, frac)))

    def _redraw(self, active=None):
        if active is not None:
            self._active = active
        c = self
        c.delete("all")
        if self._last - self._first >= 0.999:
            return                          # 内容放得下 → 完全隐形
        h = c.winfo_height()
        if h < 10:
            return
        ty, tl = self._thumb_geom()
        x0 = (self._track_w - self._thumb_t) / 2.0
        col = self._colors["fg"] if self._active else self._colors["fg_muted"]
        round_rect(c, x0, ty, x0 + self._thumb_t, ty + tl, self._thumb_t / 2.0,
                   fill=col, outline=col, width=0)

    def _apply(self):
        self._bg = self._colors["bg"]
        self.configure(bg=self._bg)
        self._redraw()


# --------------------------------------------------------------------------- #
#  圆角滑杆（细轨道 + 圆钮）
# --------------------------------------------------------------------------- #

class RoundedScale(ThemedMixin, tk.Canvas):
    """Canvas 自绘滑杆：细圆角轨道 + 圆形滑钮。

    ttk.Scale 在 clam 下只能画成「方槽 + 方块钮」（实测放大后就是两个直角矩形），
    和圆角卡片摆在一起很扎眼，所以一并自绘。

    用法与 ttk.Scale 一致：
        sc = RoundedScale(parent, colors, fonts, variable=self.vars['quality'],
                          from_=1, to=100, length=140, command=...)
    额外提供 set(value)；绑定的变量被外部改动（如套用模板）时会自动重绘。
    """

    def __init__(self, parent, colors, fonts, variable, from_=0.0, to=100.0,
                 length=140, command=None, height=24, bg=None):
        self._colors = colors
        self._fonts = dict(fonts)
        self.variable = variable
        self._from = float(from_)
        self._to = float(to)
        self.command = command
        self._knob_r = 7.0
        # 轨道两端各留出滑钮半径，不然 0/100 时圆钮会被裁掉一半
        self._pad = self._knob_r + 2.0
        self._bg_literal = bg
        self._bg = bg or colors["surface"]
        self._drag = False
        self._active = False
        super().__init__(parent, width=length, height=height, bg=self._bg,
                         highlightthickness=0, bd=0, takefocus=1)
        self.bind("<Configure>", lambda e: self._redraw())
        self.bind("<ButtonPress-1>", self._on_press)
        self.bind("<B1-Motion>", self._on_drag)
        self.bind("<ButtonRelease-1>", self._on_release)
        self.bind("<Enter>", lambda e: self._redraw(active=True))
        self.bind("<Leave>", lambda e: self._redraw(active=False))
        self.bind("<FocusIn>", lambda e: self._redraw(active=True))
        self.bind("<FocusOut>", lambda e: self._redraw(active=False))
        self.bind("<Left>", lambda e: self._nudge(-1))
        self.bind("<Right>", lambda e: self._nudge(1))
        try:
            self.variable.trace_add("write", lambda *a: self._redraw())
        except AttributeError:                       # 老版本 Tk 只有 trace()
            self.variable.trace("w", lambda *a: self._redraw())

    # -- 对外 --
    def set(self, value):
        self._write(float(value))

    def get(self):
        return self._value()

    # -- 取值 / 写值 --
    def _value(self):
        try:
            v = float(self.variable.get())
        except (TypeError, ValueError):
            return self._from
        return max(self._from, min(self._to, v))

    def _write(self, v, notify=True):
        v = max(self._from, min(self._to, v))
        try:
            self.variable.set(int(round(v)) if isinstance(self.variable, tk.IntVar) else v)
        except tk.TclError:
            return
        if notify and self.command:
            self.command()
        self._redraw()

    def _nudge(self, step):
        self._write(self._value() + step)
        return "break"                               # 别让方向键再传给页面

    # -- 鼠标 --
    def _on_press(self, e):
        self.focus_set()
        self._drag = True
        self._set_from_x(e.x)

    def _on_drag(self, e):
        if self._drag:
            self._set_from_x(e.x)

    def _on_release(self, e):
        self._drag = False

    def _set_from_x(self, x):
        w = self.winfo_width()
        span = max(1.0, w - 2 * self._pad)
        frac = max(0.0, min(1.0, (x - self._pad) / span))
        self._write(self._from + frac * (self._to - self._from))

    # -- 绘制 --
    def _redraw(self, active=None):
        if active is not None:
            self._active = active
        c = self
        c.delete("all")
        w, h = c.winfo_width(), c.winfo_height()
        if w <= 2 * self._pad + 4 or h < 8:
            return
        p = self._colors
        cy = h / 2.0
        tr = 3.0                                     # 轨道半高 = 圆角半径
        x0, x1 = self._pad, w - self._pad
        round_rect(c, x0, cy - tr, x1, cy + tr, tr, fill=p["slider_track"],
                   outline=p["slider_track"], width=0, tags="track")
        frac = (self._value() - self._from) / max(1e-6, self._to - self._from)
        kx = x0 + frac * (x1 - x0)
        if kx > x0 + 0.5:                            # 已选部分
            round_rect(c, x0, cy - tr, kx, cy + tr, tr, fill=p["accent"],
                       outline=p["accent"], width=0, tags="fill")
        r = self._knob_r
        c.create_oval(kx - r, cy - r, kx + r, cy + r, fill=p["slider_knob"],
                      outline=p["slider_knob_edge"],
                      width=2 if self._active else 1, tags="knob")

    def _apply(self):
        self._bg = self._bg_literal or self._colors["surface"]
        self.configure(bg=self._bg)
        self._redraw()
