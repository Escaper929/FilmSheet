# -*- coding: utf-8 -*-
"""把底片册页面渲染成 PNG —— 直接从 Tk 控件树读几何自绘，不做屏幕捕获。

用途：在拿不到前台窗口（多显示器、辅助功能权限受限、CI 环境）时，
仍能"看见"底片册长什么样。

跑法：
    FS_THEME=light FS_OUT=/tmp/a.png python3 tests/preview_archive_ui.py
    FS_THEME=dark  FS_OUT=/tmp/b.png python3 tests/preview_archive_ui.py
    FS_PREVIEW_FONT=/path/to/font.ttc python3 tests/preview_archive_ui.py

两条硬约定
----------
1. **所有坐标都从真实控件读**（winfo_rootx/rooty/width/height），字号也读控件
   真实使用的字体。早期版本把工具栏与详情面板的坐标**手写在代码里**，于是预览
   图与真实界面越差越远：曾把两行工具栏画成一行、把详情面板画到 x=1235 宽 159
   （真实是 x=1108 宽 286），药丸和下拉框叠在一起，看着像布局坏了。
   手写坐标的图只能表达「设计意图」，不能当布局依据。
2. **不写用户配置**。原实现为了切主题会把 ui_theme 存进真实配置 —— 跑一次预览
   就把用户的主题设置改了。现在只改内存里的调色板。

仍然是**近似还原**：自绘控件（Canvas 图元）按真实几何 + 主题色重绘，而不是读
屏幕上原始像素；图标只画示意。用于确认布局、层级、信息密度够不够；像素级验证
仍需真实运行，布局不变量以 tests/selftest_archive_ui.py 的程序化断言为准
（例如「详情面板缩略图必须真的显示出来」「最后一个操作按钮必须可达」）。
"""
import glob
import os
import sys

# 仓库根目录由 __file__ 推出来。**别写死绝对路径**：写死的后果有两层 ——
# 一是把本机用户名带进了公开仓库，二是换台机器/换个克隆目录就直接 ImportError。
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.chdir(ROOT)

import tkinter as tk
import tkinter.messagebox as MB
from tkinter import font as tkfont
from tkinter import ttk

MB.showinfo = MB.showwarning = MB.showerror = lambda *a, **k: None

from ui.app import App, THEMES
from ui.rounded import PillButton, _button_palette
from PIL import Image, ImageDraw, ImageFont

WANT_W, WANT_H = 1400, 860
THEME = os.environ.get("FS_THEME", "")

root = tk.Tk()
app = App(root)
# 只在内存里换主题（见文件头约定 2）。App 构造时已按配置文件选好调色板，
# 这里直接覆盖并重新应用，不落盘。
if THEME in THEMES and THEMES[THEME].get("name") != app.colors.get("name"):
    app.colors = THEMES[THEME]
    app._apply_theme(app.colors)

root.geometry(f"{WANT_W}x{WANT_H}+0+0")
app.archive_page.refresh()
app._show_page(4)
for _ in range(10):
    root.update_idletasks()
    root.update()

ap = app.archive_page
ap.load_visible_thumbs()
for _ in range(10):
    root.update_idletasks()
    root.update()
# 选中第一卷：详情面板只在选中后才有内容，不选就画不出它
ap.select(ap._cards[0][1].id)
for _ in range(8):
    root.update_idletasks()
    root.update()

# 画布尺寸取**窗口真实客户区**，不写死请求值：写死的话，一旦窗口没按预期的
# 尺寸布局（被屏幕钳制、被 WM 改尺寸），预览就在边上留一大片空白或把内容切掉，
# 而图上看不出是「窗口小」还是「布局空」。
W, H = root.winfo_width(), root.winfo_height()
img = Image.new("RGB", (W, H), app.colors["bg"])
d = ImageDraw.Draw(img)
R = root


# --------------------------------------------------------------------------- #
#  几何：一律换算自真实控件
# --------------------------------------------------------------------------- #
#
# 基准就取 root.winfo_rootx/rooty —— 它返回的正是**客户区（内容区）左上角**。
#
# ⚠️ 别拿 wm geometry() 里的 +x+y 当基准：macOS 上它报的是**窗口边框（含标题栏）**
# 的位置，和 winfo_rooty 差一个标题栏高度（本机实测 32px）。用它做基准会把整页
# 上移 32px、底部操作栏被推出画布，而只差 32px 很容易被当成"渲染没问题"。
# 判定依据（沿屏幕垂直线用 winfo_containing 逐点探测）：Tk 内容占据屏幕
# y = winfo_rooty .. winfo_rooty + winfo_height，此前为空 →
# 62 .. 922，而 geometry 报的 +30 只是边框位置。
# 另一条独立佐证：底部操作栏按客户区算落在 y=793..849，底边 849 = 860 − 11
# （它的 pady 下边距）严丝合缝；按 +30 算则是 825..881，一个 pack 出来的控件
# 不可能溢出窗口 21px。

def gx(w):
    return w.winfo_rootx() - R.winfo_rootx()


def gy(w):
    return w.winfo_rooty() - R.winfo_rooty()


def box(w):
    """控件在窗口坐标系里的 (x0, y0, x1, y1)。"""
    return (gx(w), gy(w), gx(w) + w.winfo_width(), gy(w) + w.winfo_height())


def bg_of(w, fallback=None):
    try:
        return w.cget("bg")
    except Exception:
        return fallback if fallback is not None else app.colors["bg"]


def visible(w) -> bool:
    """控件是否真的在窗口里。

    未选中的详情面板会把缩略图整条 pack_forget，此时 winfo_* 返回 1×1 的
    陈旧值 —— 不挡掉就会在左上角画出一个小方块。
    """
    try:
        return bool(w.winfo_exists()) and w.winfo_manager() != "" and \
            w.winfo_width() > 1 and w.winfo_height() > 1
    except tk.TclError:
        return False


# --------------------------------------------------------------------------- #
#  字体
# --------------------------------------------------------------------------- #

# 「Tk 家族名 → PIL 字体文件」对照表。
#
# ⚠️ 这张表存在的唯一理由：**Tk 只能按家族名取字体，PIL 只能按文件路径取**，
# 两套标识不互通。缺了它，预览会和界面用上两套不同的中文字体（预览挑到
# Hiragino，界面用 PingFang），字宽、行高、换行位置全对不上 —— 预览就不是
# 「所见即所得」，而是另一份看起来差不多的图。
#
# 具体取文件里的第几个字面由 _open_face 按「家族名 + 字重名」扫出来，
# **不记索引**：同一台机器上 STHeiti Medium.ttc 是 0=Heiti TC / 1=Heiti SC，
# Songti.ttc 是 0=Black / 1=Bold / 3=Light，位置毫无规律。
_FAMILY_FILES = {
    # 较新的 macOS 已不再提供 /System/Library/Fonts/PingFang.ttc，PingFang
    # 改由 MobileAsset 下发，路径里带内容哈希（每次更新都会变）→ 必须 glob。
    "PingFang SC": ["/System/Library/Fonts/PingFang.ttc",
                    "/System/Library/AssetsV2/com_apple_MobileAsset_Font8/"
                    "*/AssetData/PingFang.ttc"],
    "Hiragino Sans GB": ["/System/Library/Fonts/Hiragino Sans GB.ttc"],
    "Heiti SC": ["/System/Library/Fonts/STHeiti Medium.ttc",
                 "/System/Library/Fonts/STHeiti Light.ttc"],
    "STHeiti": ["/System/Library/Fonts/STHeiti Medium.ttc"],
    "Songti SC": ["/System/Library/Fonts/Supplemental/Songti.ttc",
                  "/System/Library/Fonts/Songti.ttc"],
    "Arial Unicode MS": ["/Library/Fonts/Arial Unicode.ttf",
                         "/System/Library/Fonts/Supplemental/Arial Unicode.ttf"],
}

# 家族名认不出来（或对应文件全缺席）时按路径硬扫的兜底顺序。
# 一律要求有中文字形，Helsvetica.ttc 这种无 CJK 的只在最后一档凑数。
_FONT_FILE_FALLBACK = [
    "/System/Library/Fonts/Hiragino Sans GB.ttc",
    "/System/Library/Fonts/STHeiti Medium.ttc",
    "/System/Library/Fonts/STHeiti Light.ttc",
    "/Library/Fonts/Arial Unicode.ttf",
    "/System/Library/Fonts/Helvetica.ttc",     # 无中文，仅保证有字可画
]

# style 字符串五花八门（"Semibold"/"W6"/"Bold"…），只能按关键词排优先级。
_STYLE_WORDS = {
    True:  ("semibold", "bold", "medium", "heavy", "black", "regular", "light"),
    False: ("regular", "medium", "book", "light", "semibold", "bold"),
}


def _has_cjk(f) -> bool:
    """这个字面有没有中文字形？

    缺字形时 FreeType 交回 .notdef，所以拿一个必然缺字的私有区码位一比即可
    —— 只看「bbox 非空」不够，豆腐块本身也有 bbox。
    """
    try:
        return f.getbbox("中") != f.getbbox("\ue000")
    except Exception:
        return False


def _paths(pats):
    """把（可能含通配符的）路径列表展开成真实存在的文件列表。"""
    out = []
    for p in pats:
        if "*" in p:
            out.extend(sorted(glob.glob(p)))
        elif os.path.exists(p):
            out.append(p)
    return out


def _open_face(path, px, family, bold):
    """从字体文件里扫出 `family` 家族、尽量贴近 `bold` 要求的那个字面。

    .ttc 是「一个文件装多个字面」，index 是字面序号。位置没有规律（见
    _FAMILY_FILES 的注释），所以按家族名 + 字重名扫，扫不到同族就退回第一个。
    family=None 表示「不挑家族」，取文件里第一个可用的字面。
    """
    words = _STYLE_WORDS[bool(bold)]
    best = (len(words) + 1, None)
    for idx in range(24):                       # 字面数远小于 24，扫不动即止
        try:
            f = ImageFont.truetype(path, px, index=idx)
            fam, style = f.getname()
        except Exception:
            break
        if family and fam != family:
            continue
        sl = (style or "").lower()
        rank = len(words)
        for i, w in enumerate(words):
            if w in sl:
                rank = i
                break
        if rank < best[0]:
            best = (rank, f)
            if rank == 0:
                break
    return best[1]


def _font_px(px, bold=False):
    """按**像素**字号挑一个真有中文字形的字体。

    顺序是「先按界面真正选中的家族找，再按路径兜底」。界面用 _pick_font 按
    **家族名**选字体（Tk 没有别的途径），PIL 只能按**文件**加载，两者靠
    _FAMILY_FILES 对接 —— 直接按路径列表找的话，预览挑 Hiragino、界面用
    PingFang，图就和真实界面不是同一个东西了。

    也不能只留 PingFang.ttc：较新的 macOS 已不再提供这个文件，那样每次都回落
    到 Helvetica.ttc（无 CJK），于是**所有中文渲染成豆腐块**，预览图完全看不出
    文案。想让某台机器指定字体就设 FS_PREVIEW_FONT。
    """
    if os.environ.get("FS_PREVIEW_FONT"):
        f = _open_face(os.environ["FS_PREVIEW_FONT"], px, None, bold)
        if f:
            return f
    fam = getattr(app, "font", None)            # App 真正选中的家族名
    for path in _paths(_FAMILY_FILES.get(fam, ())):
        f = _open_face(path, px, fam, bold)
        if f and _has_cjk(f):
            return f
    # 兜底：先只接受有中文字形的，实在没有才退回「有字可画」的（避免豆腐块，
    # 但总比 load_default 强）
    for require_cjk in (True, False):
        for path in _paths(_FONT_FILE_FALLBACK):
            f = _open_face(path, px, None, bold)
            if f and (not require_cjk or _has_cjk(f)):
                return f
    return ImageFont.load_default()


# Tk 的点值→像素换算：macOS 上是 96/72 = 4/3。
# 用真实缩放比而不是猜常数：实测同一文本与 Tk 的 measure() 宽度差在 1px 内
# （9pt→37/36、10pt→39/39、11pt→46/45、12pt→49/48、13pt→51/51、15pt→60/60）。
SCALE = float(root.call("tk", "scaling"))


def font(pt, bold=False):
    """按**点值**取字体 —— 与 app.fonts 里的写法一致（如 (F, 11) 即 11pt）。"""
    return _font_px(max(6, round(abs(pt) * SCALE)), bold)


def F_spec(spec, fallback_pt=10):
    """按某个控件**真实使用的** Tk 字体规格造 PIL 字体。

    比在预览里另写一套字号表可靠：界面改字号时预览跟着变。
    """
    try:
        tf = tkfont.Font(root=R, font=spec)
        return font(tf.actual("size"), tf.actual("weight") == "bold")
    except Exception:
        return font(fallback_pt)


def TW(txt, f):
    """文本的**前进宽度**，用来排版定宽/居中。

    别用 textbbox：它量的是字形**墨迹**的外框，而中文字形的墨迹比它的前进宽度
    窄 —— 用墨迹宽度定药丸/按钮宽度，字会溢出边框。渲染成豆腐块时两者恰好接近，
    所以换成真中文字体后才暴露出来。
    """
    return d.textlength(txt, font=f)


def rgb(c):
    try:
        if isinstance(c, str) and c.startswith("#"):
            return tuple(int(c[i:i + 2], 16) for i in (1, 3, 5))
    except Exception:
        pass
    return (200, 200, 200) if not isinstance(c, str) or c == "white" else (255, 255, 255)


# --------------------------------------------------------------------------- #
#  绘制原语
# --------------------------------------------------------------------------- #

def put(txt, x, y, f, color, anchor="lm"):
    d.text((x, y), txt, font=f, fill=color, anchor=anchor)


def line_h(f) -> int:
    try:
        a, b = f.getmetrics()
        return a + b
    except Exception:
        return 16


def wrapped(txt, f, maxw):
    """按前进宽度折行（真实控件靠 wraplength 换行，预览得自己折）。

    不折的话长值会横穿卡片边界 —— 预览图上看不出是"截断"还是"越界"。
    """
    if maxw <= 10 or TW(txt, f) <= maxw:
        return [txt]
    out, cur = [], ""
    for ch in txt:
        if TW(cur + ch, f) > maxw and cur:
            out.append(cur)
            cur = ch
        else:
            cur += ch
    if cur:
        out.append(cur)
    return out


def paint_label(w, color=None):
    """按真实几何画一个 tk.Label（文字、颜色、字号全取自控件本身）。"""
    if not visible(w):
        return
    txt = w.cget("text")
    if not txt:
        return
    x0, y0, x1, y1 = box(w)
    f = F_spec(w.cget("font"))
    col = color or rgb(w.cget("fg"))
    try:
        wrap_at = int(w.cget("wraplength") or 0)
    except Exception:
        wrap_at = 0
    lines = wrapped(txt, f, min(wrap_at or 10 ** 9, x1 - x0))[:2]
    lh = line_h(f)
    anchor = (w.cget("anchor") or "w")
    cy = y0 + (y1 - y0) / 2
    if len(lines) == 1:
        if anchor == "center":
            put(lines[0], (x0 + x1) / 2, cy, f, col, "mm")
        elif anchor == "e":
            put(lines[0], x1, cy, f, col, "rm")
        else:
            put(lines[0], x0, cy, f, col, "lm")
        return
    top = y0 + ((y1 - y0) - lh * len(lines)) / 2
    for i, ln in enumerate(lines):
        put(ln, x0, top + lh * i + lh / 2, f, col, "lm")


def paint_pill(w):
    """按真实几何画一个药丸按钮。

    内部布局照抄 ui/rounded.PillButton._redraw：整组（图标 + 间距 + 文字）居中，
    文字左对齐。这样预览里按钮文字的落点和真实控件一致，而不是"大概居中"。
    """
    if not visible(w):
        return
    x0, y0, x1, y1 = box(w)
    fill, fg, border, _hover = _button_palette(app.colors, w.variant, w.state)
    d.rounded_rectangle([x0, y0, x1, y1], radius=(y1 - y0) / 2,
                        fill=rgb(fill), outline=rgb(border) if border else None)
    f = F_spec(app.fonts["button"])
    tw = TW(w.text, f) if w.text else 0
    icon_w = 17 if w.icon else 0
    gap = 7 if (w.icon and w.text) else 0
    width = x1 - x0
    tx = x0 + (width - (icon_w + gap + tw)) / 2.0 + icon_w + gap
    if w.icon:
        icx = x0 + (width - (icon_w + gap + tw)) / 2.0 + icon_w / 2.0
        draw_glyph(w.icon, icx, (y0 + y1) / 2, min(9, (y1 - y0) / 2.6), fg)
    if w.text:
        put(w.text, tx, (y0 + y1) / 2 + 0.5, f, rgb(fg))


def draw_glyph(name, cx, cy, r, color):
    """图标示意（预览不做像素级图标还原，但位置与大小按真实控件算）。"""
    col = rgb(color)
    if name in ("folder",):
        d.rounded_rectangle([cx - r, cy - r * 0.75, cx + r, cy + r * 0.75],
                            radius=2, outline=col)
    elif name in ("search",):
        d.ellipse([cx - r * 0.8, cy - r * 0.8, cx + r * 0.35, cy + r * 0.35],
                  outline=col, width=max(1, int(r / 3.4)))
        d.line([cx + r * 0.3, cy + r * 0.3, cx + r * 0.9, cy + r * 0.9],
               fill=col, width=max(1, int(r / 3.4)))
    else:
        d.ellipse([cx - r * 0.6, cy - r * 0.6, cx + r * 0.6, cy + r * 0.6],
                  outline=col)


def paint_combo(w):
    """ttk.Combobox：真实几何 + 真实选中值。

    下拉箭头**画折线**，不用「⌄」这类字符 —— U+2304 在能显示中文的字体里往往
    没有字形，FreeType 会交回豆腐块（正是本项目在中文渲染上踩过的同一类坑）。
    """
    if not visible(w):
        return
    x0, y0, x1, y1 = box(w)
    c = app.colors
    d.rounded_rectangle([x0, y0, x1, y1], radius=5, fill=rgb(c["field"]),
                        outline=rgb(c["border"]))
    f = F_spec(w.cget("font"), app.fonts["body"] if isinstance(app.fonts["body"], tuple)
               else 10)
    try:
        val = w.get()
    except Exception:
        val = ""
    put(val, x0 + 8, (y0 + y1) / 2, f, rgb(c["fg"]))
    cx, cy = x1 - 14, (y0 + y1) / 2
    col = rgb(c["fg_muted"])
    d.line([cx - 4, cy - 2, cx, cy + 2], fill=col)
    d.line([cx, cy + 2, cx + 4, cy - 2], fill=col)


def paint_frame_bg(w):
    """容器底色：按真实几何铺一个直角矩形（tk.Frame 本来就没有圆角）。"""
    if visible(w):
        x0, y0, x1, y1 = box(w)
        d.rectangle([x0, y0, x1, y1], fill=rgb(bg_of(w)))


def paint_tree(frame, skip=()):
    """把一个容器里的直接子控件按真实几何画出来。

    覆盖的控件类型就是底片册用到的这几种：药丸按钮、下拉框、标签、输入框。
    容器（Frame）只铺底色 + 递归 —— 带圆角的容器（RoundedCard）由调用方显式画，
    否则直角矩形会把圆角盖掉。``skip`` 里的控件交给调用方自己画（它们需要在
    容器底色**之后**再叠内容，例如搜索框的圆角底板 + 放大镜）。
    """
    for w in frame.winfo_children():
        if w in skip:
            continue
        if isinstance(w, PillButton):
            paint_pill(w)
        elif isinstance(w, ttk.Combobox):
            paint_combo(w)
        elif isinstance(w, tk.Entry):
            paint_plain(w)
        elif isinstance(w, tk.Frame):
            paint_frame_bg(w)
            paint_tree(w)
        elif isinstance(w, tk.Label):
            paint_label(w)


def paint_plain(w):
    """tk.Entry / 其他无边框小控件：只画真实文本，不画框（框由外层画）。"""
    if not visible(w):
        return
    x0, y0, x1, y1 = box(w)
    try:
        txt = w.get()
    except Exception:
        txt = ""
    if txt:
        put(txt, x0, (y0 + y1) / 2, F_spec(w.cget("font")), rgb(w.cget("fg")))


# --------------------------------------------------------------------------- #
#  1. 顶部导航栏（自绘分段控件：真实几何 + 真实段位）
# --------------------------------------------------------------------------- #
nav = app.nav
nx0, ny0, nx1, ny1 = box(nav)
nh = ny1 - ny0
d.rounded_rectangle([nx0, ny0, nx1, ny1], radius=nh / 2,
                    fill=rgb(app.colors["seg_track"]))
items = nav.items                      # 真实导航项，不再在预览里复制一份文案
pad = nav._pad
seg = (nx1 - nx0 - pad * 2) / max(1, len(items))
cur = nav.index
d.rounded_rectangle([nx0 + pad + seg * cur + 2, ny0 + pad,
                     nx0 + pad + seg * (cur + 1) - 2, ny1 - pad],
                    radius=(nh - pad * 2) / 2, fill=rgb(app.colors["seg_thumb"]))
for i, (label, _icon) in enumerate(items):
    cx = nx0 + pad + seg * (i + 0.5)
    active = i == cur
    f = F_spec(app.fonts["nav_active"] if active else app.fonts["nav"])
    put(label, cx, (ny0 + ny1) / 2,
        f, rgb(app.colors["accent"] if active else app.colors["fg"]), "mm")

# --------------------------------------------------------------------------- #
#  2. 工具区（筛选行 + 操作行）：两行，全部按真实控件几何
# --------------------------------------------------------------------------- #
# 这里曾经是手绘的：单行、右侧固定 300px 面板，结果药丸压在下拉框上。
# 现在先铺两行的底色（它们本来就是页面底色，铺一层是为了把真实行高画出来），
# 再让 paint_tree 按真实位置画每个控件。
paint_frame_bg(ap._filter_row)
paint_frame_bg(ap._action_row)
# 搜索框要**排除在通用趟之外**：它外层是个普通 tk.Frame（没有圆角），paint_tree
# 会按直角矩形铺一层底色，把先画的圆角框和放大镜盖掉（图标就是这么消失的）。
# 所以底板、图标、输入内容都自己按顺序叠。
paint_tree(ap._filter_row, skip=(ap._search_box,))
paint_tree(ap._action_row)
sx0, sy0, sx1, sy1 = box(ap._search_box)
d.rounded_rectangle([sx0, sy0, sx1, sy1], radius=6, fill=rgb(app.colors["field"]),
                    outline=rgb(app.colors["border"]))
draw_glyph("search", gx(ap._search_icon) + ap._search_icon.winfo_width() / 2,
           (sy0 + sy1) / 2, 6, app.colors["fg_muted"])
paint_plain(ap._search_entry)

# --------------------------------------------------------------------------- #
#  3. 网格卡片：真实几何（缩略图从磁盘缓存读，与控件里那份是同一张）
# --------------------------------------------------------------------------- #
sel_id = ap._selected_id
canvas_x, canvas_y = gx(ap.canvas), gy(ap.canvas)
canvas_bottom = canvas_y + ap.canvas.winfo_height()
for card, roll in ap._cards:
    if not card.winfo_exists():
        continue
    gx0 = gx(ap.grid_host) + card.winfo_x()
    gy0 = gy(ap.grid_host) + card.winfo_y()
    cw, ch = card.winfo_width(), card.winfo_height()
    if gy0 >= canvas_bottom:
        continue
    selected = roll.id == sel_id
    d.rounded_rectangle([gx0, gy0, gx0 + cw, gy0 + ch], radius=8,
                        fill=rgb(app.colors["surface"]),
                        outline=rgb(app.colors["accent"] if selected
                                    else app.colors["border"]),
                        width=2 if selected else 1)
    # 样片区（真实控件是 tk.Canvas，缩略图是画在它上面的图元）
    ia = card.img_area
    ix0, iy0, ix1, iy1 = box(ia)
    d.rounded_rectangle([ix0, iy0, ix1, iy1], radius=7, fill=rgb(app.colors["field"]))
    tp = roll.thumbs.get("main")
    painted = False
    if tp and os.path.isfile(tp):
        try:
            tim = Image.open(tp).convert("RGB")
            tim.thumbnail((ix1 - ix0, iy1 - iy0))
            img.paste(tim, (int(ix0 + (ix1 - ix0 - tim.width) / 2),
                            int(iy0 + (iy1 - iy0 - tim.height) / 2)))
            painted = True
        except Exception:
            painted = False
    if not painted:
        # 占位：胶片齿孔意象（与 RollCard._draw_placeholder 同款）
        for yy in (iy0 + int((iy1 - iy0) * 0.32), iy0 + int((iy1 - iy0) * 0.68)):
            xx = ix0 + 12
            while xx < ix1 - 18:
                d.rectangle([xx, yy - 3, xx + 7, yy + 3],
                            fill=rgb(app.colors["border"]))
                xx += 13
        note = "未出图" if not roll.has_output() else ""
        if note:
            put(note, (ix0 + ix1) / 2, (iy0 + iy1) / 2,
                F_spec(app.fonts["caption"]), rgb(app.colors["fg_muted"]), "mm")
    # 文字区：直接用卡片里真实 Label 的几何与文本（含省略号、相机为空少一行等）
    for lab in (card.title_lbl, card.film_lbl, card.cam_lbl, card.meta_lbl):
        if lab is not None:
            paint_label(lab)

# --------------------------------------------------------------------------- #
#  4. 详情面板：真实几何 + 真实滚动视口
# --------------------------------------------------------------------------- #
# 面板是固定 286px 宽的滚动视口（见 ui/archive_page.DETAIL_W），内容比视口高，
# 所以必须**真裁剪**：先在独立图层上把整块内容画完，再只把视口那一片贴回主图。
#
# 只靠"跳过坐标在视口下方的控件"不够 —— 部分越界的控件（滚动到一半的按钮）
# 仍会把下半截画到底部操作栏上，预览里就冒出一个不该在那里的红色「删除卷宗」，
# 而真实界面上它被视口裁掉了。真实界面里视口外的内容确实是看不见的，
# 预览要么不画、要么裁掉，不能"画出来但假装看不见"。
dv = ap.detail_canvas
dx0, dy0, dx1, dy1 = box(dv)
panel = Image.new("RGB", (W, H), app.colors["bg"])
_keep_d = d
d = ImageDraw.Draw(panel)

dcard = ap.detail_card
cx0, cy0, cx1, cy1 = box(dcard)
d.rounded_rectangle([cx0, cy0, cx1, cy1], radius=12,
                    fill=rgb(app.colors["surface"]),
                    outline=rgb(app.colors["border"]))
# 卡片标题行
if dcard._title is not None:
    paint_label(dcard._title)
paint_label(ap.detail_title)
paint_label(ap.detail_sub)

# 缩略图（有产物才显示；无产物时是一条说明文字，未选中时整条被收起）
if visible(ap.detail_img):
    ix0, iy0, ix1, iy1 = box(ap.detail_img)
    tp = None
    sel = ap.service.get(sel_id) if sel_id else None
    if sel:
        tp = sel.thumbs.get("main")
    if tp and os.path.isfile(tp):
        try:
            tim = Image.open(tp).convert("RGB")
            tim.thumbnail((ix1 - ix0, iy1 - iy0))
            panel.paste(tim, (int(ix0 + (ix1 - ix0 - tim.width) / 2),
                              int(iy0 + (iy1 - iy0 - tim.height) / 2)))
        except Exception:
            pass
    elif ap.detail_img.cget("text"):
        paint_label(ap.detail_img)

# 字段（每行是「标签 + 值」两个 Label，直接按真实几何画）
for row in ap.detail_fields.winfo_children():
    paint_tree(row)
# 操作按钮
for w in ap.detail_actions.winfo_children():
    paint_pill(w)

# 只贴视口那一片
img.paste(panel.crop((dx0, dy0, dx1, dy1)), (dx0, dy0))
d = _keep_d

# 滚动条画在贴回来**之后**：它自己就覆盖在视口右缘，不该被视口裁掉
sb = ap.detail_scroll
if visible(sb) and sb._last - sb._first < 0.999:
    sx0, sy0, sx1, sy1 = box(sb)
    track_w = sb._track_w
    track_x = sx0 + (track_w - sb._thumb_t) / 2.0
    h = sy1 - sy0
    span = max(0.02, sb._last - sb._first)
    tl = max(26.0, h * span)
    ty = sy0 + (h - tl) * (sb._first / max(1e-6, 1.0 - span))
    d.rounded_rectangle([track_x, ty, track_x + sb._thumb_t, ty + tl],
                        radius=sb._thumb_t / 2.0,
                        fill=rgb(app.colors["fg_muted"]))

# --------------------------------------------------------------------------- #
#  5. 底部操作栏（固定不滚动，真实几何）
# --------------------------------------------------------------------------- #
paint_frame_bg(app.action_bar)
d.line([gx(app.btn_row), gy(app.btn_row) - 8,
        gx(app.btn_row) + app.btn_row.winfo_width(), gy(app.btn_row) - 8],
       fill=rgb(app.colors["border"]))
paint_tree(app.btn_row)

out = os.environ.get("FS_OUT", os.path.join(ROOT, ".preview_archive.png"))
img.save(out)

# 自检。两条都是"出错了也不会报错、只会静默画错"的类型，所以主动核对。
warn = []
# 1) 关键区域的真实几何必须落在画布内。坐标系一旦搞错（例如拿窗口边框当客户区），
#    图只会静默地缺一块，看起来像"底部操作栏被切了"而不是"坐标算错了"。
for label, w in (("导航", app.nav), ("工具区", ap._bar), ("网格", ap.canvas),
                 ("详情面板", ap.detail), ("底部操作栏", app.action_bar)):
    x0, y0, x1, y1 = box(w)
    if x0 < -1 or y0 < -1 or x1 > W + 1 or y1 > H + 1:
        warn.append(f"{label} 超出画布 {x0},{y0}-{x1},{y1}")

# 2) 详情面板视口**下沿以外**不能有面板内容。详情里的操作按钮比视口高，没有真
#    裁剪的话，红色的「删除卷宗」会画到底部操作栏下方 —— 而真实界面里它被视口
#    裁掉了。只扫"底栏内容之外"的两段：底栏自己有一条分隔线和一排按钮，扫到它们
#    会误报。
btn_top = gy(app.btn_row)
btn_bottom = btn_top + app.btn_row.winfo_height()
probe_rows = (list(range(int(dy1) + 2, int(btn_top) - 10)) +
              list(range(int(btn_bottom) + 1, H - 1)))
page_bg = rgb(app.colors["bg"])
probe_x = int((dx0 + dx1) / 2)
for y in probe_rows:
    px = img.getpixel((probe_x, y))
    if px != page_bg:
        warn.append(f"详情面板内容漏到视口外 y={y} 色={px}（应被视口裁掉）")
        break

_probe_font = _font_px(20)
try:
    _face = "%s %s" % _probe_font.getname()      # (家族, 字重)
except Exception:
    _face = type(_probe_font).__name__
print("已渲染:", out, img.size,
      f"主题={app.colors['name']} 字体缩放={SCALE:.3f}")
# 把真正用上的字体打出来：回落到无中文字体时预览会满屏豆腐块，但图还是「生成
# 成功」的，不打印就只能靠肉眼发现。
print(f"  Tk 家族={app.font} | PIL 字面={_face} | {getattr(_probe_font, 'path', '?')}")
for w in warn:
    print("⚠️", w)
root.destroy()
