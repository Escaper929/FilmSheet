# -*- coding: utf-8 -*-
"""归档库页面。

作为顶部导航的第 5 页，提供：
- 网格卡片浏览（缩略图 + 卷号 + 胶卷/相机 + 日期 + 张数）
- 搜索与筛选（关键词 / 胶卷 / 相机 / 年份 / 排序）
- 详情面板（打开产物、在文件夹中显示、用这卷参数重新出图、编辑、删除）
- 手动补录卷宗（允许没有产物，用于登记已拍未冲的底片）

实现要点
--------
**缩略图懒加载**：Tk 没有 img 标签，必须用 ``PhotoImage``。网格可能有几百卷，
一次性把缩略图全读进内存会直接吃爆。这里只为**当前可见区域**的卡片加载
图片，滚出视野就销毁 PhotoImage 引用。

**自绘卡片**：项目已有 RoundedCard/PillButton 体系（见 ui/rounded.py），
风格与主题跟随 THEMES，不引入第三方控件。
"""

from __future__ import annotations

import os
import subprocess
import sys
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

from ui.rounded import RoundedCard, PillButton
from ui.icons import draw_icon

# 网格卡片尺寸（含样片区 + 文字区）
#
# 文字区高度**按行数算**，不再写死。曾写死 76px，加了「相机」行后忘了同步抬高，
# 日期行被卡片底边裁掉（实测 4 行需要 85px、5 行需要 106px）。
# 写死的另一个副作用是「产物丢失」提示行无处安放 —— 它是条件出现的。
#
# 行高用「字体 linespace 实测」而非估常数：不同平台/字体的行高不同，
# 写死行高迟早又被裁。行数随内容变化（相机为空少一行、产物丢失多一行）。
CARD_W = 186
CARD_IMG_H = 124

# 基础行数：标题 / 胶卷 / 相机 / 日期+张数
CARD_LINES_BASE = 4
# 文字区内边距（上下各留一点，别贴边）
CARD_TEXT_PAD_Y = 8


def card_text_height(fonts: dict, lines: int = CARD_LINES_BASE) -> int:
    """按实际字体度量算出文字区所需高度。

    用各字体的 linespace 最大值作为行高 —— 标题是 bold，比正文高一点，
    取最大值才不会让标题那行被压扁。比写死常数可靠：不同平台/字号行高不同。
    """
    from tkinter import font as tkfont
    heights = []
    for key in ("field_label", "caption"):
        try:
            f = tkfont.Font(font=fonts.get(key, "TkDefaultFont"))
            heights.append(f.metrics("linespace"))
        except Exception:
            heights.append(18 if key == "field_label" else 15)
    line_h = max(heights) if heights else 18
    return line_h * max(1, lines) + CARD_TEXT_PAD_Y


def card_height(fonts: dict, warn: bool = False) -> int:
    """卡片总高。

    ``warn=True`` 取带「产物丢失」提示那一种的高度 —— 网格用它做行 minsize，
    保证同一行的卡片底边对齐（内容少的卡片下方留白，而不是行高忽大忽小）。
    """
    lines = CARD_LINES_BASE + (1 if warn else 0)
    return CARD_IMG_H + card_text_height(fonts, lines)

SORTS = [
    ("date_desc", "拍摄日期 ↓"),
    ("date_asc", "拍摄日期 ↑"),
    ("created_desc", "最近归档"),
    ("title", "卷号"),
    ("frames_desc", "张数"),
]

FIELD_LABELS = [
    ("roll_no", "卷号"),
    ("film", "胶卷"),
    ("camera", "相机"),
    ("shoot_date", "拍摄日期"),
    ("dev_date", "冲洗日期"),
    ("proc", "冲洗方式"),
    ("lab", "冲洗地点"),
    ("scanner", "扫描仪"),
]


def _reveal(path: str):
    """在系统文件管理器里定位文件。

    macOS 的 ``open -R`` 是「选中并显示」，Windows 是explorer /select,，
    Linux 没有等价命令，退化成打开所在目录。
    """
    if not path or not os.path.exists(path):
        return False
    try:
        if sys.platform == "darwin":
            subprocess.Popen(["open", "-R", path])
        elif sys.platform == "win32":
            subprocess.Popen(["explorer", "/select,", os.path.normpath(path)])
        else:
            subprocess.Popen(["xdg-open", os.path.dirname(path)])
        return True
    except OSError:
        return False


class ArchivePage:
    """归档库页。挂在 App 上，自身不继承 Tk 类。"""

    def __init__(self, app):
        self.app = app
        self.service = app.archive_service
        self.frame = None
        self._cards = []            # [(widget, roll)]
        self._photo_refs = {}# widget -> PhotoImage，防止被 GC 回收
        self._selected_id = ""
        # 预置换肤要用的容器引用（build 之前 apply_theme 也可能被调用）
        self._filter_row = None
        self._action_row = None
        self._search_icon = None
        self._last_cols = 0
        self._building = False     # 重建网格期间的递归保护
        self._filter_after = None  # 搜索防抖

        self.vars = {
            "keyword": tk.StringVar(),
            "film": tk.StringVar(),
            "camera": tk.StringVar(),
            "year": tk.StringVar(),
            "sort": tk.StringVar(value=SORTS[0][0]),
        }

    # ------------------------------------------------------------------ #
    #  构建
    # ------------------------------------------------------------------ #

    def build(self, parent):
        """挂到页面容器上。

        必须**直接**用 _add_page 返回的那个容器，不能再自建一层 frame：
        _add_page 返回的是 App 建的 ttk.Frame，它需要子控件来激活自己的
        尺寸计算；额外套一层 tk.Frame 会让这层 ttk.Frame 处于「无人管理」状态
        （winfo_manager() 为空），尺寸塌成 1x1，结果整页在真实运行时空白 ——
        但控件树里对象都在，所以按坐标重绘的预览脚本看不出问题。
        """
        self.frame = parent
        # 注意：parent 是 ttk.Frame，**不支持 configure(bg=...)** ——
        # ttk 走 style 机制，configure(bg) 会抛 "unknown option -bg"。
        # 这里只在自己创建的 tk 控件上设 bg，容器背景交给 App 的 TFrame style。
        self._build_toolbar(parent)
        self._build_body(parent)
        self.refresh()
        return parent

    def _build_toolbar(self, parent):
        c = self.app.colors
        bar = tk.Frame(parent, bg=c["bg"])
        bar.pack(fill=tk.X, pady=(0, 10))

        # 分两行：筛选条件一行、操作按钮一行。
        # 原来全挤在一行，窗口一窄右侧按钮就被推出可视区（截图里「归档位置」
        # 只剩半个字）。分行之后任何宽度下按钮都完整可见。
        filter_row = tk.Frame(bar, bg=c["bg"])
        filter_row.pack(fill=tk.X)
        action_row = tk.Frame(bar, bg=c["bg"])
        action_row.pack(fill=tk.X, pady=(7, 0))
        self._filter_row = filter_row
        self._action_row = action_row

        # ---- 搜索框 ----
        box = tk.Frame(filter_row, bg=c["field"], highlightthickness=1,
                       highlightbackground=c["border"])
        box.pack(side=tk.LEFT, fill=tk.Y, ipady=4, ipadx=8)
        self._search_icon = tk.Canvas(box, width=18, height=18, bg=c["field"],
                                      highlightthickness=0, bd=0)
        self._search_icon.pack(side=tk.LEFT, padx=(0, 6))
        draw_icon(self._search_icon, "search", 9, 9, 13, c["fg_muted"])

        entry = tk.Entry(box, textvariable=self.vars["keyword"], bd=0,
                         bg=c["field"], fg=c["fg"],
                         insertbackground=c["fg"],
                         font=self.app.fonts["body"],
                         highlightthickness=0, width=18)
        entry.pack(side=tk.LEFT)
        entry.bind("<Return>", lambda e: self.refresh())
        entry.bind("<KeyRelease>", self._on_search_key)

        # ---- 筛选下拉 ----
        self._filter_combos = {}
        for key, label in (("film", "全部胶卷"), ("camera", "全部相机"),
                           ("year", "全部年份")):
            var = self.vars[key]
            var.set(label)
            cb = ttk.Combobox(filter_row, textvariable=var, state="readonly",
                              width=12, font=self.app.fonts["body"])
            cb.pack(side=tk.LEFT, padx=(8, 0))
            cb.bind("<<ComboboxSelected>>", lambda e: self.refresh())
            self._filter_combos[key] = cb

        # ---- 排序 ----
        sort_cb = ttk.Combobox(filter_row, textvariable=self.vars["sort"],
                               state="readonly", width=12,
                               font=self.app.fonts["body"])
        sort_cb.pack(side=tk.LEFT, padx=(8, 0))
        sort_cb["values"] = [lbl for _, lbl in SORTS]
        sort_cb.current(0)
        sort_cb.bind("<<ComboboxSelected>>", lambda e: self.refresh())

        self.count_lbl = tk.Label(filter_row, text="", bg=c["bg"],
                                  fg=c["fg_muted"], font=self.app.fonts["caption"])
        self.count_lbl.pack(side=tk.RIGHT, padx=(12, 0))

        # ---- 操作按钮（第二行，右侧为主操作）----
        self.app._pill(action_row, text="补录卷宗", command=self.on_create_manual,
                       variant="accent", icon="plus", height=30).pack(
            side=tk.RIGHT, padx=(8, 0))
        self.app._pill(action_row, text="导入已有产物",
                       command=self.on_import_folder,
                       variant="secondary", icon="import", height=30).pack(
            side=tk.RIGHT, padx=(8, 0))
        # 归档位置属于低频但高影响操作（挪数据），视觉上做得轻一点
        self.app._pill(action_row, text="归档位置",
                       command=self.on_change_location,
                       variant="ghost", icon="folder", height=30).pack(
            side=tk.LEFT)

    def _build_body(self, parent):
        c = self.app.colors
        body = tk.Frame(parent, bg=c["bg"])
        body.pack(fill=tk.BOTH, expand=True)
        body.columnconfigure(0, weight=1)
        body.columnconfigure(1, weight=0)
        body.rowconfigure(0, weight=1)

        # ---- 左侧网格 ----
        left = tk.Frame(body, bg=c["bg"])
        left.grid(row=0, column=0, sticky="nsew")

        self.canvas = tk.Canvas(left, highlightthickness=0, bd=0,
                                bg=c["bg"])
        self.scrollbar = tk.Scrollbar(left, orient=tk.VERTICAL,
                                      command=self.canvas.yview)
        self.canvas.configure(yscrollcommand=self.scrollbar.set)
        self.scrollbar.pack(side=tk.RIGHT, fill=tk.Y)
        self.canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

        self.grid_host = tk.Frame(self.canvas, bg=c["bg"])
        self._grid_window = self.canvas.create_window(
            (0, 0), window=self.grid_host, anchor="nw")
        # 宽度由 App._layout_pages 通过 on_container_resized 喂进来。
        # 这里刻意不绑 <Configure>：首次布局时 canvas 宽度是 1，那一刻算出的
        # 列数会定型，之后再变宽也不重排（绑自己则是"自己变→改自己宽"的循环）。
        self._avail_w = 0

        # 网格内自绘滚动：只在鼠标悬于网格上时接管滚轮
        self.canvas.bind("<Enter>", lambda e: self._bind_wheel())
        self.canvas.bind("<Leave>", lambda e: self._unbind_wheel())

        self.empty_lbl = tk.Label(
            left, text="", bg=c["bg"], fg=c["fg_muted"],
            font=self.app.fonts["body"], justify=tk.LEFT)

        # ---- 右侧详情 ----
        self.detail = tk.Frame(body, bg=c["bg"], width=286)
        self.detail.grid(row=0, column=1, sticky="ns", padx=(12, 0))
        self.detail.grid_propagate(False)
        self._build_detail(self.detail)
        self._show_detail(None)

    def _build_detail(self, parent):
        c = self.app.colors
        card = RoundedCard(parent, c, self.app.fonts, title="卷宗详情", radius=12, pad=14)
        card.pack(fill=tk.X)
        self.detail_card = card
        b = card.body

        self.detail_img = tk.Label(b, bg=c["field"], text="", height=1)
        self.detail_img.pack(fill=tk.X, pady=(0, 10))

        self.detail_title = tk.Label(b, text="", bg=c["surface"], fg=c["fg"],
                                     font=self.app.fonts["card_title"],
                                     anchor="w", justify=tk.LEFT)
        self.detail_title.pack(fill=tk.X)

        self.detail_sub = tk.Label(b, text="", bg=c["surface"], fg=c["fg_muted"],
                                   font=self.app.fonts["caption"],
                                   anchor="w", justify=tk.LEFT)
        self.detail_sub.pack(fill=tk.X, pady=(2, 10))

        self.detail_fields = tk.Frame(b, bg=c["surface"])
        self.detail_fields.pack(fill=tk.X)
        self._field_labels = {}

        self.detail_actions = tk.Frame(b, bg=c["surface"])
        self.detail_actions.pack(fill=tk.X, pady=(14, 0))

        for key, label in FIELD_LABELS:
            row = tk.Frame(self.detail_fields, bg=c["surface"])
            row.pack(fill=tk.X, pady=2)
            tk.Label(row, text=label, bg=c["surface"], fg=c["fg_muted"],
                     font=self.app.fonts["caption"], width=7,
                     anchor="w").pack(side=tk.LEFT)
            val = tk.Label(row, text="", bg=c["surface"], fg=c["fg"],
                           font=self.app.fonts["body"], anchor="w",
                           justify=tk.LEFT, wraplength=190)
            val.pack(side=tk.LEFT, fill=tk.X, expand=True)
            self._field_labels[key] = val

    # ------------------------------------------------------------------ #
    #  数据刷新
    # ------------------------------------------------------------------ #

    def _current_filters(self) -> dict:
        """把下拉框的显示文案翻译回真实筛选值。

        下拉框显示「全部胶卷」这类占位文案，直接拿去做筛选会永远匹配不到。
        """
        f = {}
        kw = self.vars["keyword"].get().strip()
        if kw:
            f["keyword"] = kw
        for key in ("film", "camera", "year"):
            val = self.vars[key].get()
            if not val.startswith("全部"):
                f[key] = val
        f["sort"] = self.vars["sort"].get()
        return f

    def _on_search_key(self, _e=None):
        """输入防抖：打字过程中不刷新，避免每敲一下就重建整页。"""
        if self._filter_after:
            self.app.root.after_cancel(self._filter_after)
        self._filter_after = self.app.root.after(220, self.refresh)

    def refresh(self):
        """按当前筛选重建网格。数据源变了也调这个。"""
        if self.frame is None or not self.frame.winfo_exists():
            return
        if self._building:
            return

        warn = self.service.load_warning
        rolls = self.service.list_rolls(**self._current_filters())
        self._update_facet_options()

        self._building = True
        try:
            self._clear_grid()
            self._render_cards(rolls)
        finally:
            self._building = False

        self._update_count(rolls, warn)

        # 详情页跟着刷新，选中项没了就收起
        sel = self.service.get(self._selected_id) if self._selected_id else None
        self._show_detail(sel)

    def _update_facet_options(self):
        """刷新下拉选项（胶卷/相机/年份会随归档增长变化）。

        必须真正写回 Combobox，只改 StringVar 不会更新下拉列表。
        用户当前选中的值要保住；若该值因归档被删而消失，才退回占位文案。
        """
        facets = self.service.facets()
        if not hasattr(self, "_filter_combos"):
            return                      # 控件还没建
        for key, items in (("film", facets["films"]),
                           ("camera", facets["cameras"]),
                           ("year", facets["years"])):
            combo = self._filter_combos.get(key)
            if combo is None:
                continue
            placeholder = {"film": "全部胶卷", "camera": "全部相机",
                           "year": "全部年份"}[key]
            values = [placeholder] + list(items)
            combo["values"] = values
            cur = self.vars[key].get()
            if cur not in values:
                cur = placeholder
            self.vars[key].set(cur)

    def _update_count(self, rolls, warn: str):
        c = self.app.colors
        total = self.service.facets()["total"]
        shown = len(rolls)
        if warn:
            self.count_lbl.configure(text="⚠ " + warn, fg=c["error"])
        elif shown == total:
            self.count_lbl.configure(text=f"共 {total} 卷", fg=c["fg_muted"])
        else:
            self.count_lbl.configure(text=f"显示 {shown} / 共 {total} 卷",
                                     fg=c["fg_muted"])

    # ------------------------------------------------------------------ #
    #  网格渲染
    # ------------------------------------------------------------------ #

    def _clear_grid(self):
        """销毁所有卡片并注销它们的自绘控件。

        RollCard 本身是普通 tk.Frame，不在 _themed 里；但它内部没有自绘
        控件，所以这里只需注销可能存在的登记项，避免残留引用堆积。
        """
        for child in self.grid_host.winfo_children():
            self.app._unregister_themed(child)
            child.destroy()
        self._cards.clear()
        self._photo_refs.clear()

    def _sync_grid_width(self):
        """把可用宽度写进 canvas 里的 grid_host 窗口。

        不做这步，grid_host 只有内容本身的宽度，右侧会留一大片空白。
        """
        try:
            w = self.canvas.winfo_width()
            if w <= 1 and self._avail_w > 1:
                w = self._avail_w
            if w > 1:
                self.canvas.itemconfigure(self._grid_window, width=w)
        except tk.TclError:
            pass

    def _render_cards(self, rolls):
        self._sync_grid_width()
        if not rolls:
            self._render_empty()
            return

        self.empty_lbl.place_forget()
        # 重新布局前先清掉上一次的行列配置，否则删掉最后一张卡后
        # 遗留的 weight 会把剩余卡片拉得横向撑开
        cols = self._cols()
        self._last_cols = cols
        for c in range(cols + 8):
            self.grid_host.grid_columnconfigure(c, weight=0)
        self.grid_host.grid_columnconfigure(0, weight=0)

        fonts = self.app.fonts
        n_rows = (len(rolls) + cols - 1) // cols
        # 每行设minsize：卡片高度随内容变化（少一行相机/多一行警告），
        # 不统一的话行高会按最高的算，低的卡片下方留一段空白。
        for r in range(n_rows + 2):
            self.grid_host.grid_rowconfigure(r, weight=0,
                                             minsize=card_height(fonts, warn=True))

        for i, roll in enumerate(rolls):
            card = RollCard(self, roll, width=CARD_W)
            # sticky 只用 "n"（顶部对齐）：写 "nsew" 会把卡片拉伸到行高，
            # 而卡片高度是按内容算的，拉伸后文字区会被撑变形。
            # 列也不设 weight —— 卡片宽度固定，多出来的空间就该留白；
            # 一旦给末列 weight，它会被拉伸并把卡片推到详情面板底下去
            # （表现为最右侧那张卡被面板压住一半）。
            card.grid(row=i // cols, column=i % cols,
                      padx=5, pady=5, sticky="n")
            self._cards.append((card, roll))
        try:
            self.canvas.configure(scrollregion=self.canvas.bbox("all"))
        except tk.TclError:
            pass

    def _render_empty(self):
        c = self.app.colors
        f = self.service.facets()
        self.grid_host.grid_columnconfigure(0, weight=1)
        self.grid_host.grid_rowconfigure(0, weight=1)
        if f["total"] == 0:
            msg = ("归档库还是空的。\n\n"
                   "每次「开始生成」成功后会自动建卷；\n"
                   "已拍但还没冲扫的底片可以用「补录卷宗」先登记。")
        else:
            msg = "没有符合条件的卷宗。\n换个关键词或清掉筛选试试。"
        self.empty_lbl.configure(text=msg)
        self.empty_lbl.place(relx=0.5, rely=0.4, anchor="center")

    def _cols_for(self, avail_w: int) -> int:
        """给定网格区宽度算列数。"""
        if avail_w <= 1:
            return 3# 首帧兜底
        # 减去滚动条（11px）与两侧留白
        usable = avail_w - 24
        return max(1, min(8, usable // (CARD_W + 10)))

    def _grid_avail_width(self) -> int:
        """网格区真正可用的宽度。

        必须用网格自己的宽度，**不能**用 App 喂进来的页面宽度：页面里还有
        右侧详情面板（286px + 12px 间距）。按页面宽算列数会把卡片排到
        面板底下去 —— 表现为最右侧那张卡被面板压住一半。
        """
        try:
            w = self.canvas.winfo_width()
        except tk.TclError:
            w = 0
        if w > 1:
            return w
        # 画布还没完成布局：用页面宽减去详情面板宽估算
        if self._avail_w > 1:
            detail = 0
            try:
                if self.detail is not None and self.detail.winfo_exists():
                    detail = self.detail.winfo_width()
            except tk.TclError:
                detail = 0
            return max(320, self._avail_w - (detail or 300))
        return 0

    def _cols(self) -> int:
        """当前列数，按网格区实际可用宽度算。"""
        w = self._grid_avail_width()
        if w <= 1:
            return 3
        return self._cols_for(w)

    def on_container_resized(self, avail_w: int):
        """由 App 的页面布局流程告知可用宽度（已扣掉滚动条余量）。

        列数真的变了才重排 —— 每次 resize 都重建网格会让滚动位置和选中态跳动。
        """
        if not avail_w or avail_w <= 1:
            return
        # _avail_w 是**页面**宽度（含详情面板），而 _cols() 按网格自身宽度算。
        # 这里先存页面宽，等画布完成布局后 _grid_avail_width() 会给出真实值。
        self._avail_w = avail_w
        self._sync_grid_width()
        # 画布宽度就绪后才好比较列数，否则首帧宽度是 1 会算错。
        try:
            if self.canvas.winfo_width() > 1:
                cols = self._cols()
            else:
                return
        except tk.TclError:
            return
        if cols != self._last_cols:
            self._last_cols = cols
            self.refresh()

    # ---- 缩略图懒加载 ----

    def load_visible_thumbs(self):
        """只为可见区域的卡片加载缩略图，由滚动与刷新触发。

        PhotoImage 必须持有引用否则会被回收成空白 —— 所以存在 self._photo_refs。
        """
        if not self._cards:
            return
        try:
            top = self.canvas.canvasy(0)
            bottom = top + self.canvas.winfo_height()
        except tk.TclError:
            return

        for card, roll in self._cards:
            if not card.winfo_exists():
                continue
            y = card.winfo_rooty() - self.canvas.winfo_rooty()
            h = card.winfo_height()
            visible = (y + h >= top - 40) and (y <= bottom + 40)
            if visible and not card.has_image() and roll.has_output():
                card.load_image()

    # ---- 滚轮 ----

    def _bind_wheel(self):
        self.canvas.bind_all("<MouseWheel>", self._on_wheel)
        self.canvas.bind_all("<Button-4>", self._on_wheel_linux)
        self.canvas.bind_all("<Button-5>", self._on_wheel_linux)

    def _unbind_wheel(self):
        self.canvas.unbind_all("<MouseWheel>")
        self.canvas.unbind_all("<Button-4>")
        self.canvas.unbind_all("<Button-5>")

    def _on_wheel(self, e):
        self.canvas.yview_scroll(int(-1 * (e.delta / 120)), "units")
        self.load_visible_thumbs()

    def _on_wheel_linux(self, e):
        d = -1 if e.num == 4 else 1
        self.canvas.yview_scroll(d, "units")
        self.load_visible_thumbs()

    # ------------------------------------------------------------------ #
    #  选中与详情
    # ------------------------------------------------------------------ #

    def select(self, roll_id: str):
        self._selected_id = roll_id
        for card, roll in self._cards:
            card.set_selected(roll.id == roll_id)
        self._show_detail(self.service.get(roll_id) if roll_id else None)

    def _show_detail(self, roll):
        c = self.app.colors
        if roll is None:
            for key, _ in FIELD_LABELS:
                self._field_labels[key].configure(text="")
            self.detail_title.configure(text="未选中卷宗")
            self.detail_sub.configure(text="点击左侧卡片查看详情")
            self._clear_image(self.detail_img)
            self._rebuild_actions(None)
            return

        self.detail_title.configure(text=roll.title)
        sub = roll.subtitle
        bits = [b for b in (sub, roll.shoot_date) if b]
        meta = " · ".join(bits)
        if roll.frame_count:
            meta += f" · {roll.frame_count} 张"
        self.detail_sub.configure(text=meta)

        for key, _ in FIELD_LABELS:
            self._field_labels[key].configure(text=getattr(roll, key, "") or "—")

        self._load_detail_image(roll)
        self._rebuild_actions(roll)

    def _load_detail_image(self, roll):
        c = self.app.colors
        path = self.service.ensure_thumb(roll)
        if not path:
            self._clear_image(self.detail_img)
            if not roll.has_output():
                # 明确区分「没出图」和「图丢了」，这两种都要让用户知道
                self.detail_img.configure(
                    text="（产物文件不在了）" if roll.outputs else "（未出图）",
                    image="", fg=c["fg_muted"])
            else:
                self.detail_img.configure(text="", image="", fg=c["fg_muted"])
            return
        try:
            from PIL import Image, ImageTk
            im = Image.open(path)
            im.thumbnail((258, 190), Image.LANCZOS)
            photo = ImageTk.PhotoImage(im)
            self.detail_img.configure(image=photo, text="")
            self._detail_photo = photo      # 保持引用
        except Exception as e:
            self.detail_img.configure(text=f"缩略图读取失败\n{e}", image="",
                                      fg=c["error"])

    @staticmethod
    def _clear_image(label):
        label.configure(image="", text="")
        try:
            label._photo = None
        except Exception:
            pass

    def _rebuild_actions(self, roll):
        """按可用产物重建按钮组。没有产物时按钮要真的消失，
        而不是留一个点了报错的灰按钮。

        重建前必须把旧按钮从 App 的 _themed 名单里注销 —— 否则它们被 destroy
        之后仍留在名单里，下次换肤遍历到就会抛 "invalid command name"，
        整个换肤流程中断（表现为点一下主题切换就崩溃）。
        """
        for child in self.detail_actions.winfo_children():
            self.app._unregister_themed(child)
            child.destroy()

        if roll is None:
            return

        def add(text, cmd, icon=None, variant="secondary"):
            self.app._pill(self.detail_actions, text=text, command=cmd,
                           variant=variant, icon=icon, height=30,
                           min_width=120, on_card=True).pack(
                fill=tk.X, pady=3)

        existing = roll.existing_outputs()
        if existing:
            add("打开产物", lambda: self._open_output(existing[0]), "eye")
            add("在文件夹中显示", lambda: _reveal(existing[0].path), "folder")
            # 一卷里通常有两种风格产物，单独列出方便直接对比
            for o in existing[1:3]:
                style = {"lightbox": "灯板",
                         "contact_sheet": "接触印相"}.get(o.style, o.style or "产物")
                add(f"打开{style}", (lambda p: lambda: self._open_output_by(p))(o.path),
                    "eye")
        elif roll.outputs:
            # 登记过产物但文件不在了：说明白，不要给一个必然失败的按钮
            tk.Label(self.detail_actions, text="产物文件已不在原位置",
                     bg=c["surface"], fg=c["warning"],
                     font=self.app.fonts["caption"]).pack(fill=tk.X, pady=2)
            add("在文件夹中显示", lambda: _reveal(roll.outputs[0].path), "folder")

        add("用这卷参数重新出图", lambda: self.app.reload_from_archive(roll),
            "play", variant="accent")
        add("添加产物文件…", lambda: self.on_add_output(roll), "import")
        add("编辑信息…", lambda: self.on_edit(roll), "grid")
        add("删除卷宗", lambda: self.on_delete(roll), "trash", variant="danger")

    def _open_output(self, output):
        self._open_output_by(output.path)

    def _open_output_by(self, path):
        if not path or not os.path.isfile(path):
            messagebox.showwarning("文件不存在",
                                   f"找不到这个文件：\n{path}\n\n"
                                   f"它可能已被移动或删除。")
            return
        try:
            if sys.platform == "darwin":
                subprocess.Popen(["open", path])
            elif sys.platform == "win32":
                os.startfile(path)
            else:
                subprocess.Popen(["xdg-open", path])
        except OSError as e:
            messagebox.showerror("打不开", f"无法打开这个文件：\n{e}")

    # ------------------------------------------------------------------ #
    #  动作
    # ------------------------------------------------------------------ #

    def on_create_manual(self):
        RollEditor(self.app, self, None, on_saved=self.refresh)
        return

    def on_change_location(self):
        """更改归档库位置。

        这是最容易把数据"弄丢"的操作：配置一改，旧路径下的文件还在，但应用
        会像凭空消失一样看不见它们。所以流程必须是**先复制、再切配置、最后
        提示手动清理**，绝不能直接改完配置就宣布成功。
        """
        from archive import store as S
        from utils.helpers import load_config, save_config

        cur = S.archive_dir()
        is_default = not load_config().get("archive_dir")
        dlg = tk.Toplevel(self.app.root)
        dlg.title("归档库位置")
        dlg.transient(self.app.root)
        dlg.resizable(False, False)

        c = self.app.colors
        body = tk.Frame(dlg, bg=c["bg"], padx=18, pady=16)
        body.pack(fill=tk.BOTH, expand=True)

        card = RoundedCard(body, c, self.app.fonts, title="归档库位置", radius=12, pad=14)
        card.pack(fill=tk.X)
        inner = card.body
        inner.columnconfigure(0, weight=1)

        tk.Label(inner, text="当前位置", bg=c["surface"], fg=c["fg_muted"],
                 font=self.app.fonts["caption"], anchor="w").grid(
            row=0, column=0, columnspan=2, sticky=tk.W)
        path_lbl = tk.Label(inner, text=cur, bg=c["surface"], fg=c["fg"],
                            font=self.app.fonts["body"], anchor="w",
                            wraplength=340, justify=tk.LEFT)
        path_lbl.grid(row=1, column=0, columnspan=2, sticky=tk.W, pady=(2, 10))

        tk.Label(inner, text="新位置", bg=c["surface"], fg=c["fg_muted"],
                 font=self.app.fonts["caption"], anchor="w").grid(
            row=2, column=0, columnspan=2, sticky=tk.W)
        new_var = tk.StringVar()
        entry = tk.Entry(inner, textvariable=new_var, bg=c["field"], fg=c["fg"],
                         insertbackground=c["fg"], font=self.app.fonts["body"],
                         highlightthickness=1, highlightbackground=c["border"],
                         highlightcolor=c["accent"])
        entry.grid(row=3, column=0, sticky=tk.EW, pady=(2, 8))

        def browse():
            d = filedialog.askdirectory(title="选择归档库位置")
            if d:
                new_var.set(d)

        def restore_default():
            new_var.set("")
            path_lbl.configure(text=cur)

        self.app._pill(inner, text="浏览…", command=browse,
                       variant="secondary", height=28, min_width=70,
                       on_card=True).grid(row=3, column=1, sticky=tk.E, padx=(8, 0))
        self.app._pill(inner, text="恢复默认", command=restore_default,
                       variant="ghost", height=28, min_width=78,
                       on_card=True).grid(row=4, column=1, sticky=tk.E, pady=(0, 4))

        hint = tk.Label(
            body,
            text=("归档数据会复制到新位置，旧文件保留不动。\n"
                  "确认新位置的数据无误后，可手动删除旧目录。\n"
                  "留空则恢复默认位置（与配置文件同目录）。"),
            bg=c["bg"], fg=c["fg_muted"], font=self.app.fonts["caption"],
            justify=tk.LEFT, wraplength=372)
        hint.pack(fill=tk.X, pady=(10, 0))

        btns = tk.Frame(body, bg=c["bg"])
        btns.pack(fill=tk.X, pady=(12, 0))
        cancel_btn = self.app._pill(btns, text="取消", command=dlg.destroy,
                                    variant="secondary", height=32)
        cancel_btn.pack(side=tk.RIGHT)
        save_btn = self.app._pill(btns, text="迁移到这里",
                                  command=self._do_change_location,
                                  variant="accent", height=32)
        save_btn.pack(side=tk.RIGHT, padx=(0, 8))
        for b in (cancel_btn, save_btn):
            b.bind("<Destroy>",
                   lambda e, w=b: self.app._unregister_themed(w))

        self._pending_new_dir = new_var.get
        self._loc_dialog = dlg
        self._center_dialog(dlg, 408, 372)
        entry.focus_set()

    @staticmethod
    def _center_dialog(win, w, h):
        win.update_idletasks()
        sw, sh = win.winfo_screenwidth(), win.winfo_screenheight()
        w, h = min(w, sw - 60), min(h, sh - 120)
        win.geometry(f"{w}x{h}+{max(0,(sw-w)//2)}+{max(0,(sh-h)//3)}")

    def _do_change_location(self):
        """执行迁移：复制 → 改配置 → 重载 → 报告。"""
        import shutil
        from archive import store as S
        from utils.helpers import load_config, save_config

        raw = (self._pending_new_dir() or "").strip()
        new_dir = os.path.abspath(os.path.expanduser(raw)) if raw else S.default_config_dir()
        old_dir = S.archive_dir()
        old_file = os.path.join(old_dir, S.ARCHIVE_FILENAME)

        if os.path.normpath(new_dir) == os.path.normpath(old_dir):
            messagebox.showinfo("位置未变", "新旧位置相同，无需迁移。",
                                parent=self._loc_dialog)
            return

        target_file = os.path.join(new_dir, S.ARCHIVE_FILENAME)
        if os.path.exists(target_file):
            if not messagebox.askyesno(
                    "目标已有归档数据",
                    f"目标位置已存在归档文件：\n{target_file}\n\n"
                    f"继续会覆盖它。建议先备份。\n确定继续吗？",
                    parent=self._loc_dialog):
                return

        # 1) 先复制。失败就不该改配置。
        if os.path.isfile(old_file):
            try:
                os.makedirs(new_dir, exist_ok=True)
                shutil.copy2(old_file, target_file)
            except OSError as e:
                messagebox.showerror("迁移失败",
                                     f"复制归档数据失败，已保持原位置不变：\n{e}",
                                     parent=self._loc_dialog)
                return

        # 2) 再改配置。配置写失败要回滚拷贝，避免两份数据各说各话。
        cfg = load_config()
        cfg["archive_dir"] = new_dir if new_dir != S.default_config_dir() else ""
        if not save_config(cfg):
            # save_config 内部吞异常，这里只能靠复核文件确认
            verify = load_config().get("archive_dir", "") or S.default_config_dir()
            if os.path.normpath(verify) != os.path.normpath(new_dir):
                try:
                    if os.path.exists(target_file) and not os.path.exists(old_file):
                        os.unlink(target_file)
                except OSError:
                    pass
                messagebox.showerror("迁移失败",
                                     "无法写入配置文件，已取消迁移。",
                                     parent=self._loc_dialog)
                return

        # 3) 切换并重载
        self.service.store.load(force=True)
        self._loc_dialog.destroy()
        self.refresh()
        self.app.set_status(f"归档库已迁移至：{new_dir}", "success")
        messagebox.showinfo(
            "迁移完成",
            f"归档数据已复制到：\n{new_dir}\n\n"
            f"旧文件仍保留在：\n{old_file}\n\n"
            f"确认新数据无误后，可手动删除旧文件。",
            parent=self.app.root)

    def on_edit(self, roll):
        RollEditor(self.app, self, roll, on_saved=self.refresh)

    def on_delete(self, roll):
        if not messagebox.askyesno(
                "删除卷宗",
                f"确定从归档库移除「{roll.title}」吗？\n\n"
                f"这只删除归档记录，磁盘上的图片不会被删。\n"
                f"（如需连图一起删，请在确定后另行手动删除文件）"):
            return
        if self.service.delete(roll.id):
            self._selected_id = ""
            self.refresh()
            self.app.set_status(f"已移除卷宗：{roll.title}", "success")
            if self.service.last_error:
                messagebox.showerror("删除失败", self.service.last_error)
        else:
            messagebox.showerror("删除失败",
                                 self.service.last_error or "未知错误")

    def on_add_output(self, roll):
        paths = filedialog.askopenfilenames(
            title="选择产物文件",
            filetypes=[("图片", "*.jpg *.jpeg *.png *.tif *.tiff"), ("全部", "*.*")])
        if not paths:
            return
        added = 0
        for p in paths:
            style = ""
            n = os.path.basename(p).lower()
            if "contact" in n:
                style = "contact_sheet"
            elif "light" in n:
                style = "lightbox"
            if self.service.add_output(roll.id, p, style):
                added += 1
        self.refresh()
        self.app.set_status(f"已关联 {added} 个产物文件")

    def on_import_folder(self):
        """把一个已有目录里的图补建成卷宗（用户明确选择不做自动迁移，
        这里只提供手动触发的入口）。"""
        folder = filedialog.askdirectory(title="选择包含历史产物的文件夹")
        if not folder:
            return
        made = self.service.scan_folder(folder)
        if not self.service.last_error and made:
            self._update_facet_options()
            self.refresh()
            self.app.set_status(f"已导入 {len(made)} 卷", "success")
            messagebox.showinfo(
                "导入完成",
                f"识别出 {len(made)} 卷。\n\n"
                f"卷号等信息是从文件名推测的，建议逐卷核对一下。")
        elif made:
            messagebox.showwarning("导入有问题", self.service.last_error)

    # ------------------------------------------------------------------ #
    #  换肤
    # ------------------------------------------------------------------ #

    def apply_theme(self, colors, fonts):
        """跟随 App 换肤。卡片与详情面板各自重绘。"""
        if self.frame is None or not self.frame.winfo_exists():
            return
        # self.frame 是 ttk.Frame，不能 configure(bg=) —— ttk 走 style。
        # 它的背景由 App._apply_theme 里的 TFrame style 统一管，这里只管
        # 自己创建的 tk 控件。
        for w in (self.canvas, self.grid_host, self.detail,
                  self.empty_lbl, self.count_lbl,
                  self._filter_row, self._action_row,
                  self._search_icon):
            try:
                w.configure(bg=colors["bg"])
            except tk.TclError:
                pass
        self._clear_grid()
        self.refresh()
        self._show_detail(self.service.get(self._selected_id)
                          if self._selected_id else None)


# --------------------------------------------------------------------------- #
#  卡片
# --------------------------------------------------------------------------- #

class _Tip:
    """极简悬停提示。

    Tk 没有内置 tooltip，也没必要为此引第三方库：绑Enter/Leave 显示一个小
    Toplevel 即可。用作 mixin（动态挂方法到任意控件上），避免为了一个提示
    就把所有控件都改成继承某个基类。
    """

    def __init__(self, widget, text, delay=450):
        self._w = widget
        self._text = text
        self._delay = delay
        self._after = None
        self._tip = None
        widget.bind("<Enter>", self._schedule, add="+")
        widget.bind("<Leave>", self._hide, add="+")
        widget.bind("<Button-1>", self._hide, add="+")

    def _schedule(self, _e=None):
        self._cancel()
        try:
            self._after = self._w.after(self._delay, self._show)
        except tk.TclError:
            self._after = None

    def _cancel(self):
        if self._after is not None:
            try:
                self._w.after_cancel(self._after)
            except tk.TclError:
                pass
            self._after = None

    def _show(self):
        if self._tip is not None or not self._text:
            return
        try:
            x = self._w.winfo_rootx() + 12
            y = self._w.winfo_rooty() + self._w.winfo_height() + 4
            self._tip = tk.Toplevel(self._w)
            self._tip.wm_overrideredirect(True)
            self._tip.wm_geometry(f"+{x}+{y}")
            tk.Label(self._tip, text=self._text, justify=tk.LEFT,
                     bg="#2C2C2E", fg="#F5F5F7", padx=8, pady=5,
                     font=("TkDefaultFont", 10)).pack()
            # 提示窗也要能响应 Leave，否则鼠标移进去提示不消失
            self._tip.bind("<Leave>", self._hide)
        except tk.TclError:
            self._tip = None

    def _hide(self, _e=None):
        self._cancel()
        if self._tip is not None:
            try:
                self._tip.destroy()
            except tk.TclError:
                pass
            self._tip = None


class RollCard(tk.Frame):
    """归档库网格里的一张卷宗卡片。"""

    def __init__(self, page: ArchivePage, roll, width: int, height: int = None):
        # 行数跟着实际内容走：相机为空少一行，产物丢失多一行。
        # 写在最前面，因为高度要在 super().__init__ 之前算出来。
        c = page.app.colors
        fonts = page.app.fonts
        lines = CARD_LINES_BASE
        if not roll.camera:
            lines -= 1
        if roll.outputs and not roll.has_output():
            lines += 1
        text_h = card_text_height(fonts, lines)
        if height is None:
            height = CARD_IMG_H + text_h

        super().__init__(page.grid_host, bg=c["surface"],
                         highlightthickness=1,
                         highlightbackground=c["border"],
                         width=width, height=height)
        self.page = page
        self.roll = roll
        self._photo = None
        self._selected = False
        self.text_h = text_h
        # 这里**不**调 pack_propagate(False)：高度已按内容算准了，
        # 让它自适应可以避免「多加一个字段就裁掉底行」的老问题。
        self.img_h = CARD_IMG_H

        # ---- 样片区----
        self.img_area = tk.Canvas(self, width=width - 2, height=CARD_IMG_H,
                                  highlightthickness=0, bd=0,
                                  bg=c["field"])
        self.img_area.pack(fill=tk.X)
        self._draw_placeholder()

        # ---- 文字区 ----
        text_box = tk.Frame(self, bg=c["surface"])
        text_box.pack(fill=tk.X, padx=9, pady=(6, 2))

        self.title_lbl = tk.Label(text_box, text=roll.title, bg=c["surface"],
                                  fg=c["fg"], font=fonts["field_label"],
                                  anchor="w")
        self.title_lbl.pack(fill=tk.X)

        # 胶卷与相机分两行、各自独立省略。
        # 原来拼成一行靠字符宽度硬截，结果 "Kodak Portra 400 · Leica M6" 这种
        # 常见组合在186px 卡片里必然被砍成 "Kodak Portra 400 · Leica M" ——
        # 少了最后几个字符反而看不出是哪台相机。
        self.film_lbl = self._ellipsized_label(text_box, roll.film or "—",
                                                fonts["caption"], c)
        self.film_lbl.pack(fill=tk.X)
        # 相机为空时整行不占位：否则会留一行空白，把「日期」那行挤出去
        if roll.camera:
            self.cam_lbl = self._ellipsized_label(text_box, roll.camera,
                                                  fonts["caption"], c)
            self.cam_lbl.pack(fill=tk.X)
        else:
            self.cam_lbl = None

        bits = []
        if roll.shoot_date:
            bits.append(roll.shoot_date)
        if roll.frame_count:
            bits.append(f"{roll.frame_count} 张")
        elif not roll.outputs:
            bits.append("未出图")
        self.meta_lbl = tk.Label(text_box, text=" · ".join(bits),
                                 bg=c["surface"], fg=c["fg_muted"],
                                 font=fonts["caption"], anchor="w")
        self.meta_lbl.pack(fill=tk.X)

        # 产物丢失要看得出来，但不能盖住整张卡
        if roll.outputs and not roll.has_output():
            warn = tk.Label(text_box, text="文件不在了", bg=c["surface"],
                            fg=c["warning"], font=fonts["caption"], anchor="w")
            warn.pack(fill=tk.X)

        # 完整信息：悬停提示。省略掉的字符只能靠这个找回。
        full = " · ".join([x for x in (roll.roll_no, roll.film, roll.camera,
                                       roll.shoot_date, roll.note) if x])
        if full:
            tips = [w for w in (self, self.img_area, text_box, self.title_lbl,
                                self.film_lbl, self.cam_lbl, self.meta_lbl)
                    if w is not None]
            for w in tips:
                _Tip(w, full)

        self.bind("<Button-1>", lambda e: self.page.select(self.roll.id))
        for child in self.winfo_children():
            child.bind("<Button-1>", lambda e: self.page.select(self.roll.id))
            for sub_child in child.winfo_children():
                sub_child.bind("<Button-1>",
                               lambda e: self.page.select(self.roll.id))

    def _ellipsized_label(self, parent, text, font, colors) -> tk.Label:
        """按卡片实际可用宽度做尾部省略，而不是按字符数猜。

        字体在不同平台/字号下宽度差很多，按字符数截断必然在某些机器上砍太多。
        """
        from tkinter import font as tkfont
        lbl = tk.Label(parent, text="", bg=colors["surface"],
                       fg=colors["fg_muted"], font=font, anchor="w")
        try:
            avail = max(40, self.winfo_reqwidth() - 20)
            f = tkfont.Font(root=self, font=font)
            shown = f.measure(text)
            if shown > avail:
                ell = "…"
                while text and f.measure(text + ell) > avail:
                    text = text[:-1]
                text = text + ell
        except tk.TclError:
            pass
        lbl.configure(text=text)
        return lbl

    def _draw_placeholder(self):
        """缩略图未加载/无产物时的占位图形。"""
        c = self.page.app.colors
        self.img_area.delete("all")
        w = int(self.img_area.cget("width")) or (CARD_W - 2)
        h = CARD_IMG_H
        # 胶片齿孔意象：两条虚线，暗示「这是一卷胶片」
        for y in (h * 0.32, h * 0.68):
            x = 12
            while x < w - 12:
                self.img_area.create_rectangle(x, y - 3, x + 7, y + 3,
                                                fill=c["border"], outline="")
                x += 13
        if not self.roll.has_output():
            self.img_area.create_text(
                w / 2, h / 2, text="未出图", fill=c["fg_muted"],
                font=self.page.app.fonts["caption"])

    def has_image(self) -> bool:
        return self._photo is not None

    def load_image(self):
        """加载缩略图并显示。失败保留占位，不抛异常。"""
        path = self.page.service.ensure_thumb(self.roll)
        if not path:
            self._draw_placeholder()
            return
        try:
            from PIL import Image, ImageTk
            im = Image.open(path)
            im.thumbnail((CARD_W - 2, CARD_IMG_H), Image.LANCZOS)
            photo = ImageTk.PhotoImage(im)
            self.img_area.delete("all")
            self.img_area.create_image(
                (CARD_W - 2) / 2, CARD_IMG_H / 2, image=photo)
            self._photo = photo      # 必须持有，否则立刻被回收成空白
        except Exception:
            self._draw_placeholder()

    def set_selected(self, on: bool):
        self._selected = on
        c = self.page.app.colors
        self.configure(
            highlightbackground=c["accent"] if on else c["border"],
            highlightthickness=2 if on else 1)


# --------------------------------------------------------------------------- #
#  编辑 / 补录对话框
# --------------------------------------------------------------------------- #

class RollEditor:
    """手动补录与编辑卷宗。薄对话框，字段与拍摄信息页保持一致。"""

    def __init__(self, app, page: ArchivePage, roll, on_saved=None):
        self.app = app
        self.page = page
        self.service = page.service
        self.roll = roll
        self.on_saved = on_saved
        self.vars = {}

        dlg = tk.Toplevel(app.root)
        self.dlg = dlg
        dlg.title("编辑卷宗" if roll else "补录卷宗")
        dlg.transient(app.root)
        dlg.resizable(False, False)
        self._center(dlg, 400, 470)

        c = app.colors
        body = tk.Frame(dlg, bg=c["bg"], padx=18, pady=16)
        body.pack(fill=tk.BOTH, expand=True)

        card = RoundedCard(body, c, app.fonts,
                           title="拍摄信息" if roll else "新建卷宗档案",
                           radius=12, pad=14)
        card.pack(fill=tk.BOTH, expand=True)
        inner = card.body
        inner.columnconfigure(1, weight=1)

        initial = roll or None
        for i, (key, label) in enumerate(FIELD_LABELS):
            var = tk.StringVar(value=getattr(initial, key, "") if initial else "")
            self.vars[key] = var
            tk.Label(inner, text=label, bg=c["surface"], fg=c["fg_muted"],
                     font=app.fonts["caption"], width=8,
                     anchor="w").grid(row=i, column=0, sticky=tk.W, pady=3)
            tk.Entry(inner, textvariable=var, bg=c["field"], fg=c["fg"],
                     insertbackground=c["fg"], font=app.fonts["body"],
                     highlightthickness=1, highlightbackground=c["border"],
                     highlightcolor=c["accent"]).grid(
                row=i, column=1, sticky=tk.EW, padx=(8, 0), pady=3)

        note_row = len(FIELD_LABELS)
        self.vars["note"] = tk.StringVar(value=(roll.note if roll else ""))
        tk.Label(inner, text="备注", bg=c["surface"], fg=c["fg_muted"],
                 font=app.fonts["caption"], width=8,
                 anchor="w").grid(row=note_row, column=0, sticky=tk.NW, pady=3)
        tk.Text(inner, height=3, bg=c["field"], fg=c["fg"],
                font=app.fonts["body"], highlightthickness=1,
                highlightbackground=c["border"], wrap=tk.WORD,
                insertbackground=c["fg"]).grid(
            row=note_row, column=1, sticky=tk.EW, padx=(8, 0), pady=3)
        self.note_text = inner.winfo_children()[-1]
        if roll and roll.note:
            self.note_text.insert("1.0", roll.note)

        hint = tk.Label(body, text="允许不填任何产物 —— 已拍未冲的底片也可以先登记。",
                        bg=c["bg"], fg=c["fg_muted"], font=app.fonts["caption"],
                        justify=tk.LEFT, wraplength=364)
        hint.pack(fill=tk.X, pady=(10, 0))

        btns = tk.Frame(body, bg=c["bg"])
        btns.pack(fill=tk.X, pady=(12, 0))
        cancel_btn = app._pill(btns, text="取消", command=dlg.destroy,
                               variant="secondary", height=32)
        cancel_btn.pack(side=tk.RIGHT)
        save_btn = app._pill(btns, text="保存", command=self._save,
                             variant="accent", height=32)
        save_btn.pack(side=tk.RIGHT, padx=(0, 8))
        # 对话框关闭时注销自绘控件，否则留在 App._themed 里，
        # 下次换肤会遍历到已销毁的按钮而抛 TclError。
        for b in (cancel_btn, save_btn):
            dlg.protocol("WM_DELETE_WINDOW",
                         lambda b=b: (app._unregister_themed(b), dlg.destroy()))
            b.bind("<Destroy>", lambda e, b=b: app._unregister_themed(b))

        var = self.vars["roll_no"]
        var.set("")
        dlg.after(60, lambda: self.note_text.focus_set())

    def _center(self, win, w, h):
        win.update_idletasks()
        sw, sh = win.winfo_screenwidth(), win.winfo_screenheight()
        w, h = min(w, sw - 60), min(h, sh - 120)
        x = max(0, (sw - w) // 2)
        y = max(0, (sh - h) // 3)
        win.geometry(f"{w}x{h}+{x}+{y}")

    def _save(self):
        fields = {k: v.get().strip() for k, v in self.vars.items() if k != "note"}
        fields["note"] = self.note_text.get("1.0", "end").strip()

        if not any(fields.get(k) for k, _ in FIELD_LABELS):
            messagebox.showwarning("信息为空",
                                   "至少填写卷号或胶卷中的一项。",
                                   parent=self.dlg)
            return

        try:
            if self.roll:
                self.service.update(self.roll.id, **fields)
                msg = f"已更新卷宗：{fields.get('roll_no') or fields.get('film')}"
            else:
                made = self.service.create_manual(fields)
                if made is None:
                    messagebox.showerror("保存失败",
                                         self.service.last_error or "未知错误",
                                         parent=self.dlg)
                    return
                msg = f"已新建卷宗：{made.title}"
        except Exception as e:
            messagebox.showerror("保存失败", str(e), parent=self.dlg)
            return

        if self.service.last_error:
            messagebox.showwarning("已保存，但有警告", self.service.last_error,
                                   parent=self.dlg)
        self.dlg.destroy()
        if self.on_saved:
            self.on_saved()
        self.app.set_status(msg, "success")