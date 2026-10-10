# -*- coding: utf-8 -*-

import os
import sys
import threading
import json
import ctypes
import tkinter as tk
from tkinter import filedialog, messagebox, ttk
from filmsheet._version import __VERSION__
from utils.helpers import load_config, save_config, add_pack_image_history, LABEL_MAP, INFO_LAYOUT, NO_COLON_FIELDS, FILM_FORMAT_RATIOS
from ui.rounded import (RoundedCard, PillButton, SegmentedControl,
                        SlimScrollbar, RoundedScale)
from ui.icons import draw_icon
from archive.service import ArchiveService
from ui.archive_page import ArchivePage
from processor.config_schema import (
    FIELD_DEFS,
    SUB_FORMATS_135,
    SUB_FORMATS_BY_FORMAT,
    THUMB_WIDTH_MAX,
    THUMB_WIDTH_MIN,
    default_sub_format,
)

# 页面标题 + 分段导航图标（顺序即导航顺序）
PAGES = [
    ("输入与输出", "import"),
    ("画幅与排版", "grid"),
    ("外观", "palette"),
    ("拍摄信息", "camera"),
    ("归档库", "archive"),
]

# ::: 界面双主题调色板（macOS 风格） :::
# 浅色 = 系统灰白 + 白色卡片 + iOS 蓝强调；深色 = 近黑 + 深灰卡片 + 亮蓝强调
# 扁平、弱边框、靠色彩分层，呼应 macOS 系统外观。
THEMES = {
    "light": {
        "name": "浅色",
        "bg":        "#F5F5F7",   # 系统灰白（macOS 浅色窗口底）
        "surface":   "#FFFFFF",   # 白色卡片
        "field":     "#FFFFFF",   # 输入框内部
        "fg":        "#1D1D1F",   # 近黑主文字
        "fg_muted":  "#86868B",   # 次要文字（系统灰）
        "disabled":  "#B0B0B5",
        "accent":    "#0071E3",   # 苹果蓝强调
        "accent_fg": "#FFFFFF",
        "accent_hover":  "#1A7FEB",
        "accent_active": "#0062C4",
        "seg_track": "#E9E9EC",   # 分段导航轨道
        "seg_thumb": "#FFFFFF",   # 分段导航选中药丸
        "slider_track":     "#E2E2E6",   # 自绘滑杆轨道
        "slider_knob":      "#FFFFFF",   # 自绘滑杆圆钮
        "slider_knob_edge": "#C9C9CF",   # 圆钮描边（白钮在浅底上需要一圈定形）
        "border":    "#E2E2E6",   # 细描边（浅灰）
        "hover":     "#EDEDF0",
        "active":    "#E3E3E8",
        "success":   "#34C759",
        "warning":   "#FF9500",
        "error":     "#FF3B30",
    },
    "dark": {
        "name": "深色",
        "bg":        "#1E1E20",   # 近黑（macOS 深色窗口底）
        "surface":   "#2C2C2E",   # 深灰卡片
        "field":     "#3A3A3C",   # 输入框
        "fg":        "#F5F5F7",   # 系统白文字
        "fg_muted":  "#98989F",   # 次要文字（深色系统灰）
        "disabled":  "#5B5B60",
        "accent":    "#0A84FF",   # 亮蓝强调
        "accent_fg": "#FFFFFF",
        "accent_hover":  "#2E93FF",
        "accent_active": "#0069D9",
        "seg_track": "#2C2C2E",
        "seg_thumb": "#48484C",
        "slider_track":     "#48484C",   # 深底上的轨道要比卡片亮一档才看得见
        "slider_knob":      "#F2F2F5",   # 深色下保持近白圆钮（与 macOS 一致）
        "slider_knob_edge": "#636369",
        "border":    "#3F3F46",   # 细描边
        "hover":     "#333336",
        "active":    "#3A3A3C",
        "success":   "#30D158",
        "warning":   "#FF9F0A",
        "error":     "#FF453A",
    },
}

# 字体：优先微软雅黑（Windows），退回到等线/黑体/默认
def _pick_font(root):
    from tkinter import font as tkfont
    families = set(tkfont.families(root))
    for cand in ("Microsoft YaHei UI", "微软雅黑", "Microsoft YaHei",
                 "Segoe UI", "DengXian", "Tahoma"):
        if cand in families:
            return cand
    return "TkDefaultFont"

class App:
    def __init__(self, root):
        self.root = root
        self.root.title(f"FilmSheet {__VERSION__} @Escaper")
        # 尺寸按实测取，不凭感觉给大数（原来是写死的 720x780，又窄又高，
        # 内容只占一半，底部空出一大片）：
        #   宽 900 —— 两栏卡片各留得下「标签 + 输入框 + 按钮」一行
        #   高 510 —— 最高页面(画幅与排版 382) + 导航/操作栏 chrome(118) ≈ 500，留 10 余量
        # 两个环境变量只用于出图对比，正常使用不受影响。
        _w = int(os.environ.get("FILMSHEET_WIN_W", 900))
        _h = int(os.environ.get("FILMSHEET_WIN_H", 510))
        self._center_window(_w, _h)
        # 允许继续缩小：小屏/高缩放的机器上窗口必须放得下。
        # 再往下缩内容就超出可视区了，此时覆盖式细滚动条会自动浮现。
        self.root.minsize(820, 420)

        cfg = load_config()
        self.pack_history = cfg.get("pack_images", [])

        # 归档库：领域层无 Tk 依赖，这里只做实例持有。
        # 自动建卷默认开启，可在归档库页面关掉。
        self.archive_service = ArchiveService()
        self.archive_service.enabled = cfg.get("archive_auto", True)
        self.archive_page = ArchivePage(self)

        # 确保子画幅与画幅匹配。必须按 film_format 校验，不能用「135+120 并集」
        # 放行——否则 film_format=120 配 sub_format='标准 36×24' 会静默通过，
        # 最终按默认比例出图。这与 API 侧 _validate_sub_format 是同一条规则。
        film_format = cfg.get("film_format") or FIELD_DEFS["film_format"]["default"]
        sub_format = cfg.get("sub_format") or default_sub_format(film_format)
        if sub_format not in SUB_FORMATS_BY_FORMAT.get(film_format, SUB_FORMATS_135):
            sub_format = default_sub_format(film_format)

        # 同理夹回缩略图宽度：Spinbox 只能产出合法值，但配置文件可能是旧版
        # 写进去的（上限曾一度是 1600）。不夹的话点「开始生成」只会撞上
        # validate_config 的「配置错误」，用户看不出是自己存了个越界值。
        thumb_width = cfg.get("thumb_width") or FIELD_DEFS["thumb_width"]["default"]
        if not (THUMB_WIDTH_MIN <= thumb_width <= THUMB_WIDTH_MAX):
            thumb_width = max(THUMB_WIDTH_MIN, min(THUMB_WIDTH_MAX, thumb_width))

        self.vars = {
            'input_folder': tk.StringVar(),
            'output_file': tk.StringVar(value="filmsheet_output.jpg"),
            'thumb_width': tk.IntVar(value=thumb_width),
            'spacing': tk.IntVar(value=20),
            'columns': tk.IntVar(value=6),
            'force_landscape': tk.BooleanVar(value=True),
            'edge_text': tk.StringVar(value=""),
            'output_format': tk.StringVar(value="JPG"),
            'quality': tk.IntVar(value=95),
            'film_format': tk.StringVar(value="135"),
            'sub_format': tk.StringVar(value=sub_format),
            'info_lang': tk.StringVar(value="en"),
            'pack_image': tk.StringVar(),
            'pack_position': tk.StringVar(value=cfg.get("pack_position", "left")),
            'pack_border_stroke': tk.BooleanVar(value=cfg.get("pack_border_stroke", True)),
            'processing_mode': tk.StringVar(value="positive"),
            'perf_mode': tk.StringVar(value="Auto"),
            'render_style': tk.StringVar(value=cfg.get("render_style", "lightbox")),
            'pack_size': tk.IntVar(value=cfg.get("pack_size", 80)),
            'signature': tk.StringVar(value=cfg.get("signature", "")),
            'batch_export_enabled': tk.BooleanVar(value=cfg.get("batch_export_enabled", False)),
            'current_template': tk.StringVar(value=cfg.get("current_template", "")),
            'single_photo_show_extra': tk.BooleanVar(value=cfg.get("single_photo_show_extra", False)),
        }

        self.vars['single_photo_mode'] = tk.BooleanVar(value=False)
        self.single_image_files = []  # New: store file paths directly (list, not StringVar)
        # Keep single_image_path StringVar for compatibility with FilmProcessor's _resolve_image_list()
        self.vars['single_image_path'] = tk.StringVar(value="")

        for key in LABEL_MAP:
            self.vars[f'info_{key}'] = tk.StringVar()

        self.processor = None
        self.info_labels = {}
        self.pending_roll = None      # 渲染成功后回写的卷宗（主线程用）
        self.font = _pick_font(root)          # 主字体（微软雅黑）
        self._font_widgets = []               # 需随主题更新字体的控件
        self._pages = []                      # 页面 wrapper 列表
        self._page_titles = []                # [(标题, 图标名)] 供分段导航使用
        self._current_page = 0
        self._themed = []                     # 自绘控件清单，换肤时统一刷新
        # 从配置恢复界面主题（默认浅色）
        cfg = load_config()
        self.colors = THEMES.get(cfg.get("ui_theme", "light"), THEMES["light"])
        # 字体层级必须在 build_ui 之前就绪（自绘控件构造时需要）
        self.fonts = self._build_fonts(self.colors)
        self.build_ui()
        self._apply_theme(self.colors)

    # ---------------- 字体层级 ----------------
    def _layout_pages(self):
        """同步页面容器的宽高与滚动区。

        宽度跟随画布：否则窗口变宽时右侧空出一片、卡片也不会跟着变宽。
        高度不小于可视区：短页面时让 scrollregion 恰好等于可视区，
        避免内容放得下却还能滚动几像素的「幽灵滚动」。
        """
        canvas = self.canvas
        try:
            if not canvas.winfo_exists():
                return
            w = canvas.winfo_width()
            h = canvas.winfo_height()
        except tk.TclError:
            return
        if w <= 1:
            return
        natural = 0
        if 0 <= self._current_page < len(self._pages):
            natural = self._pages[self._current_page].winfo_reqheight()
        canvas.itemconfigure(self._pages_window, width=w)
        canvas.itemconfigure(self._pages_window, height=max(natural, h))
        try:
            canvas.configure(scrollregion=canvas.bbox("all"))
        except tk.TclError:
            pass
        # 归档库内部有自己的网格，靠这里把实际可用宽度喂给它。
        # 不用它自己绑 <Configure>：首次布局时宽度是 1，算出的列数会定型，
        # 之后 canvas 再变宽也不会重排。
        try:
            if getattr(self, "archive_page", None) is not None:
                self.archive_page.on_container_resized(w)
        except tk.TclError:
            pass

    def _center_window(self, w, h):
        """把窗口放到屏幕中上部，并钳制在屏幕内（避免底部被任务栏盖住）。"""
        self.root.update_idletasks()
        sw, sh = self.root.winfo_screenwidth(), self.root.winfo_screenheight()
        w = min(w, max(640, sw - 40))
        h = min(h, max(430, sh - 120))
        x = max(0, (sw - w) // 2)
        y = max(0, (sh - h) // 4)
        self.root.geometry(f"{w}x{h}+{x}+{y}")

    def _apply_titlebar_theme(self, dark):
        """让 Windows 标题栏跟随主题。

        标题栏是 DWM 画的，Tk 完全管不着 —— 不设置这个，切到深色后
        窗口内容全是黑的、标题栏却还是白的，观感上「窗口颜色不对」。
        属性 20 对应 Win10 20H1+ / Win11，更早的 Win10 要用 19。
        """
        if sys.platform != "win32":
            return
        if getattr(self, "_titlebar_unsupported", False):
            return
        try:
            user32, dwmapi = ctypes.windll.user32, ctypes.windll.dwmapi
            hwnd = user32.GetAncestor(self.root.winfo_id(), 2)   # 2 = GA_ROOT
            if not hwnd:
                return
            val = ctypes.c_int(1 if dark else 0)
            ok = False
            for attr in (20, 19):
                if dwmapi.DwmSetWindowAttribute(hwnd, attr,
                                                ctypes.byref(val),
                                                ctypes.sizeof(val)) == 0:
                    ok = True
                    break
            if not ok:
                self._titlebar_unsupported = True   # 太老的系统，只提示一次
                print("提示：此 Windows 版本不支持深色标题栏（DwmSetWindowAttribute 失败）",
                      file=sys.stderr)
                return
            # DWM 改完属性标题栏不一定立刻重绘，逼一次框架重算
            user32.SetWindowPos(hwnd, 0, 0, 0, 0, 0, 0x0041)  # NOSIZE|NOMOVE|FRAMECHANGED
        except Exception as e:
            print("设置标题栏主题失败: %s" % e, file=sys.stderr)
            self._titlebar_unsupported = True

    def _build_fonts(self, p):
        """字号/字重层级：页面标题 > 卡片标题 > 正文 > 次要说明。

        改版前所有文字都是同一个 9pt，导致界面没有视觉层级 —— 这是「显旧」的主因之一。
        """
        F = self.font
        return {
            "page_title":  (F, 15, "bold"),
            "card_title":  (F, 12, "bold"),
            "body":        (F, 10),
            "caption":     (F, 9),
            "button":      (F, 11),
            "nav":         (F, 11),
            "nav_active":  (F, 11, "bold"),
            "field_label": (F, 10, "bold"),
            "big_number":  (F, 13, "bold"),
        }

    # ---------------- 便捷构造（自动登记，换肤时统一刷新） ----------------
    @staticmethod
    def _widget_alive(w) -> bool:
        """控件是否仍存在（Tk destroy 之后 winfo_exists 会抛而不是返回 False）。"""
        try:
            return bool(w.winfo_exists())
        except (tk.TclError, AttributeError):
            return False

    def _unregister_themed(self, widget):
        """把已销毁的控件从换肤名单里摘掉。

        动态创建又重建的控件（归档库卡片、详情按钮、对话框按钮）生命周期
        短于 App，不注销就会在下次换肤时抛 "invalid command name"，
        把整个 _apply_theme 打断，界面停在半换肤状态。
        """
        lst = getattr(self, "_themed", None)
        if lst is None:
            return
        try:
            lst.remove(widget)
        except ValueError:
            pass

    def _card(self, parent, title="", title_icon=None, radius=12, pad=15):
        c = RoundedCard(parent, self.colors, self.fonts, title=title,
                        title_icon=title_icon, radius=radius, pad=pad)
        self._themed.append(c)
        return c

    def _card_body(self, parent, title="", title_icon=None, radius=12, pad=15):
        """建卡片并直接返回其内容容器（多数调用点只用得到 body）。"""
        return self._card(parent, title, title_icon, radius, pad).body

    def _pill(self, parent, text="", command=None, variant="secondary",
              icon=None, height=34, min_width=0, bg=None, on_card=False):
        """药丸按钮。放在圆角卡片里时要传 on_card=True，
        否则按钮底色会取页面底色，在白卡片上会露出一块灰底。"""
        b = PillButton(parent, self.colors, self.fonts, text=text, command=command,
                       variant=variant, icon=icon, height=height,
                       min_width=min_width, bg=bg, on_card=on_card)
        self._themed.append(b)
        return b

    def _card_pill(self, parent, text="", command=None, variant="secondary",
                   icon=None, height=28, min_width=0):
        """卡片内的小号药丸按钮（比操作栏的 34px 略小，贴合行高）。"""
        return self._pill(parent, text=text, command=command, variant=variant,
                          icon=icon, height=height, min_width=min_width,
                          on_card=True)

    def _get_label_text(self, key):
        lang = self.vars['info_lang'].get()
        idx = 0 if lang == 'zh' else 1
        text = LABEL_MAP[key][idx]
        if key in NO_COLON_FIELDS:
            return text
        return text + ":"

    def _update_info_labels(self, *args):
        for key, label_widget in self.info_labels.items():
            label_widget.config(text=self._get_label_text(key))

    def browse_pack_image(self):
        path = filedialog.askopenfilename(
            title="选择胶卷包装图片",
            filetypes=[("图片文件", "*.jpg *.jpeg *.png *.bmp *.tiff")]
        )
        if path:
            self.pack_history = add_pack_image_history(path)
            self.vars['pack_image'].set(path)
            self.refresh_pack_combo()

    def clear_pack_image(self):
        self.vars['pack_image'].set("")

    def refresh_pack_combo(self):
        existing_paths = [p for p in self.pack_history if os.path.exists(p)]
        self.pack_history = existing_paths
        current = self.vars['pack_image'].get()
        values = ["(无)"] + existing_paths
        self.pack_combo['values'] = values
        if current in existing_paths:
            self.pack_combo.set(current)
        elif not current:
            self.pack_combo.set("(无)")

    def on_pack_combo_change(self, event=None):
        val = self.vars['pack_image'].get()
        if val == "(无)":
            self.vars['pack_image'].set("")

    def _on_style_changed(self):
        """Called when render_style radiobutton changes."""
        self.save_pack_config()
        self._update_batch_checkbox_label()

    def _update_batch_checkbox_label(self):
        """Update batch export checkbox text based on current render_style."""
        style = self.vars['render_style'].get()
        if style == "contact_sheet":
            self.batch_cb.config(text="同时生成灯板正片版")
        else:
            self.batch_cb.config(text="同时生成接触印相版")

    def save_pack_config(self, event=None):
        cfg = load_config()
        cfg["pack_position"] = self.vars['pack_position'].get()
        cfg["pack_border_stroke"] = self.vars['pack_border_stroke'].get()
        cfg["render_style"] = self.vars['render_style'].get()
        cfg["pack_size"] = self.vars['pack_size'].get()
        cfg["signature"] = self.vars['signature'].get()
        cfg["batch_export_enabled"] = self.vars['batch_export_enabled'].get()
        save_config(cfg)

    def build_ui(self):
        outer = ttk.Frame(self.root)
        outer.pack(fill=tk.BOTH, expand=True)

        # ================= 固定底部操作栏（不随滚动，主操作始终可见） =================
        action_bar = tk.Frame(outer, bg=self.colors["bg"], highlightthickness=0)
        self.action_bar = action_bar
        action_bar.pack(side=tk.BOTTOM, fill=tk.X, padx=16, pady=(0, 11))

        # 空闲时不显示进度条（避免底部常驻一条空槽），开始生成时再 pack 出来
        self.progress_bar = ttk.Progressbar(action_bar, orient=tk.HORIZONTAL, mode='determinate')

        btn_row = tk.Frame(action_bar, bg=self.colors["bg"], highlightthickness=0)
        self.btn_row = btn_row
        btn_row.pack(fill=tk.X, pady=(12, 0))
        self.status_lbl = tk.Label(btn_row, text="FilmSheet Ready",
                                   bg=self.colors["bg"], fg=self.colors["fg_muted"],
                                   font=self.fonts["caption"], anchor="w")
        self.status_lbl.pack(side=tk.LEFT)

        self.theme_btn = self._pill(btn_row, text="浅色", command=self.toggle_theme,
                                    variant="ghost", icon="sun", height=30)
        self.theme_btn.pack(side=tk.LEFT, padx=(14, 0))

        self.preview_btn = self._pill(btn_row, text="预览", command=self.preview_process,
                                      variant="secondary", icon="eye")
        self.preview_btn.pack(side=tk.RIGHT, padx=(8, 0))
        self.cancel_btn = self._pill(btn_row, text="取消", command=self.cancel_process,
                                     variant="secondary", icon="x")
        self.cancel_btn.pack(side=tk.RIGHT, padx=8)
        self.start_btn = self._pill(btn_row, text="开始生成", command=self.start_process,
                                    variant="accent", icon="play", min_width=136)
        self.start_btn.pack(side=tk.RIGHT)
        self.cancel_btn.set_state(tk.DISABLED)

        # ================= 顶部导航（自绘分段控件，贴合 macOS 系统外观） =================
        nav_bar = tk.Frame(outer, bg=self.colors["bg"], highlightthickness=0)
        self.nav_bar = nav_bar
        nav_bar.pack(side=tk.TOP, fill=tk.X, padx=16, pady=(11, 2))

        self.nav = SegmentedControl(nav_bar, self.colors, self.fonts,
                                    [(t, i) for t, i in PAGES],
                                    command=self._show_page, height=38)
        self.nav.pack(fill=tk.X)
        self._themed.append(self.nav)

        # ================= 可滚动内容区（页面） =================
        canvas = tk.Canvas(outer, highlightthickness=0, bd=0)
        self.canvas = canvas
        canvas.configure(bg=self.colors["bg"], highlightbackground=self.colors["bg"])
        canvas.pack(side=tk.TOP, fill=tk.BOTH, expand=True)

        # 页面容器：各 tab 作为其子 Frame，用 pack 切换显示
        self.pages_host = tk.Frame(canvas, bg=self.colors["bg"])
        self._pages_window = canvas.create_window((0, 0), window=self.pages_host, anchor="nw")

        self._layout_pages()

        def _sync_pages_width(event):
            self._layout_pages()
        canvas.bind("<Configure>", _sync_pages_width)

        def configure_scroll_region(event):
            self._layout_pages()
        self.pages_host.bind("<Configure>", configure_scroll_region)

        # 覆盖式细滚动条：以 place 浮在滚动区右侧，不占布局、不需要时完全隐形
        self.scrollbar = SlimScrollbar(canvas, self.colors, command=canvas.yview,
                                       bg=self.colors["bg"])
        self.scrollbar.place(relx=1.0, rely=0.0, anchor="ne", relheight=1.0, width=11)
        canvas.configure(yscrollcommand=self.scrollbar.set)
        # Canvas 的 lift/tkraise 都被重载成 tag_raise，提升控件层级要走原生 Tk 命令
        self.root.tk.call('raise', self.scrollbar._w)
        self._themed.append(self.scrollbar)

        def _on_mousewheel(event):
            # 只在指针位于主窗口时才滚动 —— 否则预览子窗口里的滚轮会误滚主页面
            # （原来用无条件 bind_all，属于全局劫持）
            w = self.root.winfo_containing(event.x_root, event.y_root)
            if w is None or w.winfo_toplevel() is not self.root:
                return
            canvas.yview_scroll(int(-1 * (event.delta / 120)), "units")
        self.root.bind_all("<MouseWheel>", _on_mousewheel)

        # ---- 键盘快捷键 ----
        def _on_key(event):
            if event.state & 4 and event.keysym == 'Return':  # Ctrl+Enter
                self.start_process()
            elif event.keysym == 'Escape':
                self.cancel_process()
            elif event.state & 4 and event.keysym == 'o':  # Ctrl+O
                self.browse_input()

        self.root.bind_all('<Key>', _on_key)

        # =====================================================================
        # Tab 1: 输入与输出
        # =====================================================================
        io_tab = self._add_page("输入与输出")
        io_tab.columnconfigure(0, weight=1)
        io_tab.columnconfigure(1, weight=1)

        c = self._card(io_tab, "输入", "import")
        c.grid(row=0, column=0, sticky=tk.NSEW, padx=(0, 7), pady=(0, 7))
        b = c.body
        b.columnconfigure(1, weight=1)
        ttk.Label(b, text="图片来源", font=self.fonts["body"]).grid(row=0, column=0, sticky=tk.W, pady=4)
        ttk.Entry(b, textvariable=self.vars['input_folder'],
                  font=self.fonts["body"]).grid(row=0, column=1, sticky=tk.EW, padx=8)
        self._card_pill(b, text="浏览…", command=self.browse_input).grid(
            row=0, column=2, padx=(8, 0))

        # Single photo export mode — shown first so users choose input method immediately
        self.single_photo_cb = ttk.Checkbutton(
            b, text="单张照片导出", variable=self.vars['single_photo_mode'])
        self.single_photo_cb.grid(row=1, column=0, columnspan=3, sticky=tk.W, pady=(10, 3))
        ttk.Checkbutton(
            b, text="附带 包装/信息/水印",
            variable=self.vars['single_photo_show_extra']).grid(row=2, column=0, columnspan=3, sticky=tk.W, pady=3)

        ttk.Separator(b, orient=tk.HORIZONTAL).grid(row=3, column=0, columnspan=3, sticky=tk.EW, pady=11)
        ttk.Label(b, text="输出", font=self.fonts["body"]).grid(row=4, column=0, sticky=tk.W, pady=4)
        ttk.Entry(b, textvariable=self.vars['output_file'],
                  font=self.fonts["body"]).grid(row=4, column=1, columnspan=2, sticky=tk.EW, padx=8)

        c = self._card(io_tab, "输出选项", "palette")
        c.grid(row=0, column=1, sticky=tk.NSEW, padx=(7, 0), pady=(0, 7))
        b = c.body
        b.columnconfigure(1, weight=1)
        ttk.Label(b, text="格式", font=self.fonts["body"]).grid(row=0, column=0, sticky=tk.W, pady=4)
        fmt_combo = ttk.Combobox(b, textvariable=self.vars['output_format'],
                                 values=["PNG", "JPG"], state="readonly", width=8,
                                 font=self.fonts["body"])
        fmt_combo.grid(row=0, column=1, sticky=tk.W, padx=8)
        fmt_combo.bind("<<ComboboxSelected>>", self.update_ext)

        self.q_label = ttk.Label(b, text="质量", font=self.fonts["body"])
        self.q_label.grid(row=0, column=2, sticky=tk.W, padx=(20, 0))
        self.q_scale = RoundedScale(b, self.colors, self.fonts,
                                    variable=self.vars['quality'],
                                    from_=1, to=100, length=140)
        self._themed.append(self.q_scale)
        self.q_scale.grid(row=0, column=3, sticky=tk.W)
        self.q_val = ttk.Label(b, textvariable=self.vars['quality'], width=3,
                               font=self.fonts["body"])
        self.q_val.grid(row=0, column=4, sticky=tk.W)
        self.update_ext(None)

        self.batch_cb = ttk.Checkbutton(b, text="同时生成接触印相版",
                                        variable=self.vars['batch_export_enabled'])
        self.batch_cb.grid(row=1, column=0, columnspan=5, sticky=tk.W, pady=(14, 0))

        # =====================================================================
        # Tab 2: 画幅与排版
        # =====================================================================
        frame_tab = self._add_page("画幅与排版")
        frame_tab.columnconfigure(0, weight=1)
        frame_tab.columnconfigure(1, weight=1)

        c = self._card(frame_tab, "成像模式", "palette")
        c.grid(row=0, column=0, sticky=tk.NSEW, padx=(0, 7), pady=(0, 7))
        b = c.body
        ttk.Radiobutton(b, text="正片", variable=self.vars['processing_mode'],
                        value="positive").pack(anchor=tk.W, pady=3)
        ttk.Radiobutton(b, text="负片", variable=self.vars['processing_mode'],
                        value="negative").pack(anchor=tk.W, pady=3)

        c = self._card(frame_tab, "胶片模拟", "grid")
        c.grid(row=0, column=1, sticky=tk.NSEW, padx=(7, 0), pady=(0, 7))
        b = c.body
        b.columnconfigure(1, weight=1)
        ttk.Label(b, text="齿孔模式", font=self.fonts["body"]).grid(row=0, column=0, sticky=tk.W, pady=4)
        ttk.Combobox(b, textvariable=self.vars['perf_mode'],
                     values=["Auto", "KS (民用)", "BH (电影)"], state="readonly",
                     width=13, font=self.fonts["body"]).grid(row=0, column=1, sticky=tk.W, padx=8)
        ttk.Label(b, text="渲染风格", font=self.fonts["body"]).grid(row=1, column=0, sticky=tk.W, pady=6)
        style_radio = ttk.Frame(b)
        style_radio.grid(row=1, column=1, sticky=tk.W)
        ttk.Radiobutton(style_radio, text="灯板正片", variable=self.vars['render_style'],
                        value="lightbox", command=self._on_style_changed).pack(side=tk.LEFT, pady=1)
        ttk.Radiobutton(style_radio, text="接触印相", variable=self.vars['render_style'],
                        value="contact_sheet", command=self._on_style_changed).pack(side=tk.LEFT, padx=(12, 0))

        c = self._card(frame_tab, "画幅", "grid")
        c.grid(row=1, column=0, sticky=tk.NSEW, padx=(0, 7), pady=(0, 7))
        b = c.body
        b.columnconfigure(1, weight=1)
        rf = ttk.Frame(b)
        rf.grid(row=0, column=0, sticky=tk.W)
        ttk.Radiobutton(rf, text="135", variable=self.vars['film_format'], value="135",
                        command=self.toggle_sub_format).pack(side=tk.LEFT)
        ttk.Radiobutton(rf, text="120", variable=self.vars['film_format'], value="120",
                        command=self.toggle_sub_format).pack(side=tk.LEFT, padx=(12, 0))
        ttk.Label(b, text="比例", font=self.fonts["body"]).grid(row=0, column=2, sticky=tk.W, padx=(10, 0))
        self.ratio_label = ttk.Label(b, text="3:2", font=self.fonts["field_label"])
        self.ratio_label.grid(row=0, column=3, sticky=tk.W)

        ttk.Label(b, text="子画幅", font=self.fonts["body"]).grid(row=1, column=0, sticky=tk.W, pady=8)
        self.sub_combo = ttk.Combobox(b, textvariable=self.vars['sub_format'],
                                      state="readonly", width=14, font=self.fonts["body"])
        self.sub_combo.grid(row=1, column=1, sticky=tk.W, padx=8)
        self.sub_combo.bind("<<ComboboxSelected>>", lambda e: (self.update_ratio_label(), self.auto_adjust_columns()))

        # 初始化子画幅选项（避免首次点击画幅前下拉框为空）
        self.toggle_sub_format()

        c = self._card(frame_tab, "排版", "grid")
        c.grid(row=1, column=1, sticky=tk.NSEW, padx=(7, 0), pady=(0, 7))
        b = c.body
        b.columnconfigure(1, weight=1)
        ttk.Label(b, text="缩略图宽", font=self.fonts["body"]).grid(row=0, column=0, sticky=tk.W, pady=4)
        ttk.Spinbox(b, from_=THUMB_WIDTH_MIN, to=THUMB_WIDTH_MAX, textvariable=self.vars['thumb_width'], width=8,
                    font=self.fonts["body"]).grid(row=0, column=1, sticky=tk.W, padx=8)
        ttk.Label(b, text="每行列数", font=self.fonts["body"]).grid(row=1, column=0, sticky=tk.W, pady=4)
        ttk.Spinbox(b, from_=1, to=99, textvariable=self.vars['columns'], width=8,
                    font=self.fonts["body"]).grid(row=1, column=1, sticky=tk.W, padx=8)
        self._card_pill(b, text="自适应画幅", command=self.auto_adjust_columns).grid(
            row=2, column=0, columnspan=2, sticky=tk.EW, pady=(10, 6))
        ttk.Checkbutton(b, text="强制横向", variable=self.vars['force_landscape']).grid(
            row=3, column=0, columnspan=2, sticky=tk.W)

        # =====================================================================
        # Tab 3: 外观
        # =====================================================================
        look_tab = self._add_page("外观")
        look_tab.columnconfigure(0, weight=1)
        look_tab.columnconfigure(1, weight=1)

        c = self._card(look_tab, "边字设置", "grid")
        c.grid(row=0, column=0, sticky=tk.NSEW, padx=(0, 7), pady=(0, 7))
        b = c.body
        b.columnconfigure(1, weight=1)
        ttk.Label(b, text="自定义内容", font=self.fonts["body"]).grid(row=0, column=0, sticky=tk.W)
        ttk.Entry(b, textvariable=self.vars['edge_text'],
                  font=self.fonts["body"]).grid(row=0, column=1, sticky=tk.EW, padx=8)
        ttk.Label(b, text="(留空则从 '胶卷' 字段自动生成)", style="Muted.TLabel").grid(
            row=1, column=0, columnspan=2, sticky=tk.W, pady=(8, 0))

        c = self._card(look_tab, "水印签名", "grid")
        c.grid(row=0, column=1, sticky=tk.NSEW, padx=(7, 0), pady=(0, 7))
        b = c.body
        b.columnconfigure(1, weight=1)
        ttk.Label(b, text="签名", font=self.fonts["body"]).grid(row=0, column=0, sticky=tk.W)
        ttk.Entry(b, textvariable=self.vars['signature'],
                  font=self.fonts["body"]).grid(row=0, column=1, sticky=tk.EW, padx=8)
        ttk.Label(b, text="(留空则不添加水印)", style="Muted.TLabel").grid(
            row=1, column=0, columnspan=2, sticky=tk.W, pady=(8, 0))

        c = self._card(look_tab, "胶卷包装图", "grid")
        c.grid(row=1, column=0, columnspan=2, sticky=tk.NSEW, pady=(0, 7))
        b = c.body
        b.columnconfigure(1, weight=1)

        ttk.Label(b, text="图片", font=self.fonts["body"]).grid(row=0, column=0, sticky=tk.W)
        self.pack_combo = ttk.Combobox(b, textvariable=self.vars['pack_image'],
                                       state="readonly", width=24, font=self.fonts["body"])
        self.pack_combo.grid(row=0, column=1, sticky=tk.EW, padx=8)
        self.pack_combo.bind("<<ComboboxSelected>>", self.on_pack_combo_change)
        self._card_pill(b, text="浏览", command=self.browse_pack_image).grid(
            row=0, column=2, padx=(8, 6))
        self._card_pill(b, text="清除", command=self.clear_pack_image).grid(
            row=0, column=3)

        ttk.Label(b, text="位置", font=self.fonts["body"]).grid(row=1, column=0, sticky=tk.W, pady=8)
        pos_combo = ttk.Combobox(b, textvariable=self.vars['pack_position'],
                                 values=["left", "right"], state="readonly", width=6,
                                 font=self.fonts["body"])
        pos_combo.grid(row=1, column=1, sticky=tk.W, padx=8)
        pos_combo.bind("<<ComboboxSelected>>", self.save_pack_config)
        ttk.Checkbutton(b, text="描边", variable=self.vars['pack_border_stroke'],
                        command=self.save_pack_config).grid(row=1, column=2, sticky=tk.W, padx=8)

        ttk.Label(b, text="大小", font=self.fonts["body"]).grid(row=2, column=0, sticky=tk.W, pady=4)
        pack_size_scale = RoundedScale(b, self.colors, self.fonts,
                                       variable=self.vars['pack_size'],
                                       from_=10, to=100, length=180,
                                       command=self.save_pack_config)
        self._themed.append(pack_size_scale)
        pack_size_scale.grid(row=2, column=1, sticky=tk.W, padx=8)
        ttk.Label(b, textvariable=self.vars['pack_size'], width=4,
                  font=self.fonts["body"]).grid(row=2, column=2, sticky=tk.W)
        ttk.Label(b, text="%", style="Muted.TLabel").grid(row=2, column=3, sticky=tk.W)

        self.refresh_pack_combo()

        # =====================================================================
        # Tab 4: 拍摄信息
        # =====================================================================
        info_tab = self._add_page("拍摄信息")
        info_tab.columnconfigure(0, weight=1)

        card = self._card(info_tab, "模板管理", "grid")
        card.grid(row=0, column=0, sticky=tk.EW, pady=(0, 7))
        b = card.body
        b.columnconfigure(1, weight=1)
        ttk.Label(b, text="模板", font=self.fonts["body"]).grid(row=0, column=0, sticky=tk.W)
        self.tmpl_combo = ttk.Combobox(b, textvariable=self.vars['current_template'],
                                        state="readonly", width=22, font=self.fonts["body"])
        self.tmpl_combo.grid(row=0, column=1, sticky=tk.EW, padx=8)
        self.tmpl_combo.bind("<<ComboboxSelected>>", self.load_template_from_combo)
        self._card_pill(b, text="加载", command=self.load_selected_template).grid(
            row=0, column=2, padx=(8, 6))
        self._card_pill(b, text="保存", command=self.save_new_template).grid(
            row=0, column=3, padx=(0, 6))
        self._card_pill(b, text="删除", command=self.delete_selected_template).grid(
            row=0, column=4)

        card = self._card(info_tab, "拍摄信息记录（选填）", "camera")
        card.grid(row=1, column=0, sticky=tk.EW, pady=(0, 7))
        b = card.body
        b.columnconfigure(1, weight=1)
        b.columnconfigure(3, weight=1)
        b.columnconfigure(5, weight=1)

        lang_frame = ttk.Frame(b)
        lang_frame.grid(row=0, column=0, columnspan=6, sticky=tk.W, pady=(0, 10))
        ttk.Label(lang_frame, text="标签语言", font=self.fonts["body"]).pack(side=tk.LEFT, padx=(0, 8))
        lang_combo = ttk.Combobox(lang_frame, textvariable=self.vars['info_lang'],
                                  values=["zh", "en"], state="readonly", width=6,
                                  font=self.fonts["body"])
        lang_combo.pack(side=tk.LEFT)
        lang_combo.bind("<<ComboboxSelected>>", self._update_info_labels)

        # Load field histories from config into instance attributes
        field_keys = ['roll', 'camera', 'film', 'shoot_date', 'dev_date', 'proc', 'lab', 'scanner']
        cfg = load_config()
        for key in field_keys:
            hist_key = f"history_{key}"
            if not hasattr(self, hist_key):
                vals = cfg.get(hist_key, [])
                # Deduplicate while preserving order
                seen = set()
                unique_vals = []
                for v in vals:
                    if v not in seen:
                        seen.add(v)
                        unique_vals.append(v)
                setattr(self, hist_key, unique_vals)

        gui_layout = [
            [('roll', 1, 0), ('camera', 1, 2), ('film', 1, 4)],
            [('shoot_date', 2, 0), ('dev_date', 2, 2), (None, 2, 4)],
            [('proc', 3, 0), ('lab', 3, 2), ('scanner', 3, 4)]
        ]
        for row_items in gui_layout:
            for key, r, col in row_items:
                if key is None:
                    continue
                lbl = ttk.Label(b, text=self._get_label_text(key),
                                font=self.fonts["field_label"])
                lbl.grid(row=r, column=col, sticky=tk.W, padx=5, pady=3)
                self.info_labels[key] = lbl
                entry_w = 12 if key == 'roll' else 16
                hist_key = f"history_{key}"
                combo_history = getattr(self, hist_key, [])
                combo = ttk.Combobox(b, textvariable=self.vars[f'info_{key}'],
                                     values=combo_history, state="normal", width=entry_w,
                                     font=self.fonts["body"])
                combo.grid(row=r, column=col + 1, sticky=tk.EW, padx=5, pady=3)
                current_val = self.vars[f'info_{key}'].get()
                combo.set(current_val)
                combo.bind("<Return>", lambda e, k=key: self.update_field_history(k))

        # 收尾：刷新可选控件
        self._refresh_tmpl_combo()
        self._update_batch_checkbox_label()
        self.auto_adjust_columns()

        # ---- 归档库（放在最后建：它依赖前面建好的 controls 与主题）----
        archive_page = self._add_page("归档库")
        self.archive_page.build(archive_page)

        # 默认显示第一个页面
        self._show_page(0)

    def _add_page(self, title):
        """创建一个页面 wrapper；导航项由顶部 SegmentedControl 统一承载。"""
        p = self.colors
        wrapper = tk.Frame(self.pages_host, bg=p["bg"])
        page = ttk.Frame(wrapper, padding=(6, 8))
        page.pack(fill=tk.BOTH, expand=True)
        self._pages.append(wrapper)
        self._page_titles.append(title)
        return page

    def _show_page(self, idx):
        self._current_page = idx
        for i, wrapper in enumerate(self._pages):
            if i == idx:
                wrapper.pack(fill=tk.BOTH, expand=True)
            else:
                wrapper.pack_forget()
        # 更新导航态
        # 顶部导航是自绘分段控件，只需同步选中态
        if getattr(self, "nav", None) is not None:
            self.nav.set_index(idx)
        # 切页回到顶部，避免沿用上一页的滚动位置
        if getattr(self, "canvas", None) is not None:
            self.canvas.yview_moveto(0.0)
            self.root.update_idletasks()
            self._layout_pages()

    def toggle_sub_format(self):
        """切换画幅时更新子画幅选项和比例显示"""
        film_format = self.vars['film_format'].get()
        options = SUB_FORMATS_BY_FORMAT.get(film_format, SUB_FORMATS_135)
        self.sub_combo['values'] = options
        current = self.vars['sub_format'].get()
        if current not in options:
            self.vars['sub_format'].set(default_sub_format(film_format))
        self.update_ratio_label()
        self.auto_adjust_columns()  # 新增：切换画幅时自动更新列数

    # ---- 模板管理 ----

    TEMPLATE_SAVE_KEYS = [
        'film_format', 'sub_format', 'render_style', 'pack_image', 'pack_position',
        'pack_size', 'pack_border_stroke', 'processing_mode', 'thumb_width', 'columns',
        'spacing', 'force_landscape', 'perf_mode', 'output_format', 'quality',
        'signature', 'batch_export_enabled', 'edge_text',
        'single_photo_mode', 'single_photo_show_extra',
        # Info fields
        'info_roll', 'info_camera', 'info_film', 'info_shoot_date',
        'info_dev_date', 'info_proc', 'info_lab', 'info_scanner', 'info_lang',
    ]

    def _refresh_tmpl_combo(self):
        cfg = load_config()
        tmpl_names = list(cfg.get("templates", {}).keys())
        self.tmpl_combo['values'] = ["(无)"] + tmpl_names
        current = self.vars['current_template'].get()
        if current in tmpl_names:
            self.tmpl_combo.set(current)
        else:
            self.tmpl_combo.set("(无)")

    def load_template_from_combo(self, event=None):
        name = self.vars['current_template'].get()
        if name and name != "(无)":
            self.load_selected_template()

    def load_selected_template(self):
        cfg = load_config()
        name = self.vars['current_template'].get()
        if not name or name == "(无)" or name not in cfg.get("templates", {}):
            return
        tmpl = cfg["templates"][name]
        for key in self.TEMPLATE_SAVE_KEYS:
            if key in tmpl:
                var = self.vars.get(key)
                if var:
                    val = tmpl[key]
                    if isinstance(var, tk.BooleanVar):
                        var.set(bool(val))
                    elif isinstance(var, tk.IntVar):
                        try:
                            var.set(int(val))
                        except (ValueError, TypeError):
                            pass
                    else:
                        var.set(str(val) if val else "")
        # Validate pack_image path exists
        pack_img = tmpl.get('pack_image', '')
        if pack_img and not os.path.exists(pack_img):
            messagebox.showwarning("提示", f"模板中的包装图片不存在: {pack_img}")
        self.set_status(f"已加载模板: {name}")

    def save_new_template(self):
        from tkinter import simpledialog
        name = simpledialog.askstring("保存模板", "请输入模板名称:")
        if not name:
            return
        cfg = load_config()
        templates = cfg.setdefault("templates", {})
        # Collect current values for template keys
        tmpl_data = {}
        for key in self.TEMPLATE_SAVE_KEYS:
            var = self.vars.get(key)
            if var:
                val = var.get()
                if isinstance(val, bool):
                    tmpl_data[key] = val
                elif isinstance(val, int):
                    tmpl_data[key] = val
                else:
                    tmpl_data[key] = str(val) if val else ""
        templates[name] = tmpl_data
        cfg["templates"] = templates
        cfg["current_template"] = name
        save_config(cfg)
        self._refresh_tmpl_combo()
        self.set_status(f"已保存模板: {name}")

    def delete_selected_template(self):
        cfg = load_config()
        name = self.vars['current_template'].get()
        if not name or name == "(无)":
            return
        if messagebox.askyesno("确认", f"确定删除模板 '{name}' 吗?"):
            templates = cfg.get("templates", {})
            if name in templates:
                del templates[name]
            cfg["templates"] = templates
            cfg["current_template"] = ""
            self.vars['current_template'].set("")
            save_config(cfg)
            self._refresh_tmpl_combo()
            self.set_status(f"已删除模板: {name}")

    # ---- End 模板管理 ----

    def update_ratio_label(self):
        """更新比例显示"""
        film_format = self.vars['film_format'].get()
        sub_format = self.vars['sub_format'].get()
        if film_format == "135":
            ratios = {
                "标准 36×24": "3:2",
                "半格 18×24": "3:4",
                "方形 24×24": "1:1",
                "XPan 65×24": "65:24"
            }
            self.ratio_label.config(text=ratios.get(sub_format, ""))
        else:
            # 120 比例
            format_ratios = {
                "645": "1.35:1",
                "66": "1:1",
                "67": "1.25:1",
                "68": "1.37:1",
                "69": "1.5:1",
                "612": "2:1",
                "617": "3:1",
                "624": "4:1",
            }
            self.ratio_label.config(text=format_ratios.get(sub_format, ""))

    def auto_adjust_columns(self):
        """根据当前画幅自动调整每行列数"""
        film_format = self.vars['film_format'].get()
        sub_format = self.vars['sub_format'].get()
        recommended = 6  # 默认

        if film_format == "135":
            if sub_format == "标准 36×24":
                recommended = 6
            elif sub_format == "半格 18×24":
                recommended = 12
            elif sub_format == "方形 24×24":
                recommended = 8
            elif sub_format == "XPan 65×24":
                recommended = 3
        else:  # 120
            # 根据常用底片袋习惯推荐
            if sub_format in ["645"]:
                recommended = 4
            elif sub_format in ["66", "67", "68"]:
                recommended = 6
            elif sub_format in ["69", "612"]:
                recommended = 2
            elif sub_format in ["617", "624"]:
                recommended = 1

        self.vars['columns'].set(recommended)
        # Only update status label if UI is fully initialized
        if hasattr(self, 'status_lbl') and self.status_lbl is not None:
            self.set_status(f"自适应: {sub_format} → 每行 {recommended} 张")

    def update_ext(self, event):
        fmt = self.vars['output_format'].get()
        name = os.path.splitext(self.vars['output_file'].get())[0]
        self.vars['output_file'].set(f"{name}.{fmt.lower()}")
        if fmt == "PNG":
            self.q_label.grid_remove()
            self.q_scale.grid_remove()
            self.q_val.grid_remove()
        else:
            self.q_label.grid(row=0, column=2, sticky=tk.W, padx=(20,0))
            self.q_scale.grid(row=0, column=3, sticky=tk.W)
            self.q_val.grid(row=0, column=4, sticky=tk.W)

    def update_field_history(self, key):
        """Handle Enter key press on any info field - update its history."""
        field_value = self.vars[f'info_{key}'].get()
        if not field_value:
            return
        # Get or initialize history for this field
        hist_key = f"history_{key}"
        if not hasattr(self, hist_key):
            cfg = load_config()
            setattr(self, hist_key, cfg.get(hist_key, []))
        hist = getattr(self, hist_key)
        # Add value to history if not already present (and not empty)
        if field_value not in hist:
            hist.insert(0, field_value)  # Add at top
            # Limit history size to 30
            max_hist = 30
            if len(hist) > max_hist:
                hist = hist[:max_hist]
            setattr(self, hist_key, hist)
        # Save updated history to config
        cfg = load_config()
        cfg[hist_key] = hist
        save_config(cfg)

    def browse_input(self):
        if self.vars['single_photo_mode'].get():
            # In single-photo mode, allow selecting multiple files for batch single-photo export
            f = filedialog.askopenfilename(
                filetypes=[("图片文件", "*.jpg *.jpeg *.png *.tiff *.bmp")],
                multiple=True  # Allow multiple file selection
            )
            if f:
                # Store files directly in a list (not via StringVar)
                self.single_image_files = list(f)  # Convert tuple to list
                # Store as JSON string in single_image_path for compatibility with _resolve_image_list
                self.vars['single_image_path'].set(json.dumps(self.single_image_files))
                # Set input_folder to the directory of the first selected file
                self.vars['input_folder'].set(os.path.dirname(f[0]))
                # Use original filename + "_filmsheet" as the default output name
                basename = os.path.basename(f[0])
                name, ext = os.path.splitext(basename)
                output_name = f"{name}_filmsheet{ext}"
                self.vars['output_file'].set(output_name)
        else:
            # Clear single photo files when not in single-photo mode
            self.single_image_files = []
            self.vars['single_image_path'].set('')
            folder = filedialog.askdirectory()
            if folder:
                self.vars['input_folder'].set(folder)

    def preview_process(self):
        """在临时窗口中快速预览渲染效果。"""
        single_mode = self.vars['single_photo_mode'].get()
        if not single_mode:
            input_dir = self.vars['input_folder'].get()
            if not input_dir or not os.path.isdir(input_dir):
                messagebox.showwarning("提示", "请先选择图片来源文件夹！")
                return
        else:
            # Use the stored file list
            files = self.single_image_files
            if not files:
                messagebox.showwarning("提示", "请先选择要导出的照片！")
                return
            # Use the first file for preview
            sf = files[0]
            input_dir = os.path.dirname(sf)

        self.set_status("正在预览...")
        config = {k: v.get() if hasattr(v, 'get') else v for k, v in self.vars.items()}
        config['output_path'] = os.path.join(input_dir, 'preview_temp.jpg')
        threading.Thread(target=self._run_preview_worker, args=(config,), daemon=True).start()

    def _run_preview_worker(self, config):
        # The rendering pipeline imports Pillow and the renderers.  Keep that
        # work out of application startup so the main window appears quickly.
        from processor.film_processor import FilmProcessor
        proc = FilmProcessor(config)
        img, error = proc.render_preview()
        self.root.after(0, self._show_preview_result, img, error)

    def _show_preview_result(self, img, error):
        from PIL import Image

        if error:
            self.set_status("预览失败", "error")
            messagebox.showerror("预览失败", error)
            return

        if img is None:
            self.set_status("预览失败", "error")
            messagebox.showwarning("提示", "没有可处理的图片")
            return

        # 将预览图缩放到窗口可视范围内
        max_w, max_h = 900, 700
        orig_w, orig_h = img.size
        scale = min(max_w / orig_w, max_h / orig_h, 1.0)
        if scale < 1.0:
            disp_w = int(orig_w * scale)
            disp_h = int(orig_h * scale)
            img = img.resize((disp_w, disp_h), Image.LANCZOS)

        # 在临时窗口中显示
        preview_win = tk.Toplevel(self.root)
        preview_win.title(f"FilmSheet {__VERSION__} @Escaper — Preview")
        preview_win.geometry(f"{img.width + 40}x{img.height + 80}")

        # Canvas + scrollbar
        canvas_frame = ttk.Frame(preview_win)
        canvas_frame.pack(fill=tk.BOTH, expand=True, padx=10, pady=10)

        canvas = tk.Canvas(canvas_frame, bg='#333')
        scroll_y = ttk.Scrollbar(canvas_frame, orient=tk.VERTICAL, command=canvas.yview)
        scroll_x = ttk.Scrollbar(canvas_frame, orient=tk.HORIZONTAL, command=canvas.xview)
        canvas.configure(yscrollcommand=scroll_y.set, xscrollcommand=scroll_x.set)

        scroll_y.pack(side=tk.RIGHT, fill=tk.Y)
        scroll_x.pack(side=tk.BOTTOM, fill=tk.X)
        canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

        # Convert PIL Image to tkinter-compatible PhotoImage
        from PIL.ImageTk import PhotoImage as TkPhotoImage
        tk_photo = TkPhotoImage(img)

        canvas.create_image(0, 0, image=tk_photo, anchor=tk.NW, tags="preview")
        canvas.configure(scrollregion=canvas.bbox("all"))

        # 底部按钮
        btn_frame = ttk.Frame(preview_win)
        btn_frame.pack(fill=tk.X, pady=(0, 10))
        ttk.Label(btn_frame, text=f"预览尺寸: {orig_w} × {orig_h} (显示: {img.width} × {img.height})", style="Muted.TLabel").pack()
        ttk.Button(btn_frame, text="关闭", command=preview_win.destroy).pack(pady=5)

        # 保持引用防止 GC
        canvas.image_ref = tk_photo

        self.set_status("预览完成", "success")

    def start_process(self):
        single_mode = self.vars['single_photo_mode'].get()
        if not single_mode:
            input_dir = self.vars['input_folder'].get()
            if not input_dir or not os.path.isdir(input_dir):
                messagebox.showerror("Error", "请选择有效的图片来源文件夹！")
                return
        else:
            # Use the stored file list instead of StringVar
            files = self.single_image_files
            if not files:
                messagebox.showerror("Error", "请选择要导出的单张照片！")
                return
            # Get directory from first file
            input_dir = os.path.dirname(files[0])
            self.vars['input_folder'].set(input_dir)

        output_name = self.vars['output_file'].get()
        if not output_name.lower().endswith(('.png', '.jpg', '.jpeg')):
            output_name += ".jpg"

        save_dir = input_dir if input_dir else os.getcwd()
        output_path = os.path.join(save_dir, output_name)

        config = {k: v.get() if hasattr(v, 'get') else v for k, v in self.vars.items()}
        config['output_path'] = output_path

        self.start_btn.set_state(tk.DISABLED)
        self.cancel_btn.set_state(tk.NORMAL)
        self.progress_bar.pack(fill=tk.X, pady=(0, 12), before=self.btn_row)
        self.progress_bar['value'] = 0

        # Import the image-processing stack only once the user starts work.
        from processor.film_processor import FilmProcessor
        # on_done 把产物与张数回写归档库；归档失败不影响出图（processor 内部已兜住）
        self.processor = FilmProcessor(config, on_done=self._on_render_done)
        threading.Thread(target=self.run_worker, daemon=True).start()

    def _on_render_done(self, config: dict, outputs: list):
        """渲染成功后的归档回写。在工作线程里被调用，故不能直接碰 Tk。"""
        try:
            roll = self.archive_service.record_render(config, outputs)
        except Exception as e:
            print(f"归档写入失败: {e}", file=sys.stderr)
            return
        if roll is None:
            return
        # 提示语交给 process_finished 统一显示，这里只存结果。
        # 不在工作线程里调 root.after：那属于跨线程调度 Tk，
        # 现有代码也是等run() 返回后才在主线程侧刷新。
        self.pending_roll = roll

    def run_worker(self):
        upd_s = lambda m: self.root.after(0, lambda: self.set_status(m))
        upd_p = lambda v, m: self.root.after(0, lambda: [self.progress_bar.config(value=v), self.set_status(m)])
        result = self.processor.run(upd_s, upd_p)
        self.root.after(0, self.process_finished, result)

    def process_finished(self, result):
        self.start_btn.set_state(tk.NORMAL)
        self.cancel_btn.set_state(tk.DISABLED)
        self.progress_bar.pack_forget()          # 空闲时收起进度条

        if result == "success":
            self.progress_bar['value'] = 100
            self._after_archive()
            self.set_status("FilmSheet Done!", "success")
            self._show_success_dialog()
        elif result == "已取消":
            self.set_status("已取消", "warning")
        else:
            self.set_status("失败", "error")
            messagebox.showerror("Error", result)

    def _show_success_dialog(self):
        out = self.processor.config.get('output_path', '')
        msg = f"文件已保存至：\n{out}"
        roll = self.pending_roll
        if roll is not None:
            msg += f"\n\n已归档到「归档库」：{roll.title}"
        messagebox.showinfo("Success", msg)

    def _after_archive(self):
        """渲染成功后刷新归档库并提示归档结果（在主线程调用）。"""
        roll = self.pending_roll
        self.pending_roll = None
        if roll is None:
            return
        try:
            self.archive_page.refresh()
        except Exception as e:
            print(f"刷新归档库失败: {e}", file=sys.stderr)
        err = self.archive_service.last_error
        if err:
            self.set_status(f"已生成并归档，但有警告：{err}", "warning")

    # ------------------------------------------------------------------
    # 从归档库回灌参数
    # ------------------------------------------------------------------

    def reload_from_archive(self, roll):
        """把某卷的拍摄信息与渲染参数灌回参数页。

        用于「用这卷参数重新出图」。只回填档案里确实存过的字段，
        没存的保持当前值 —— 避免把用户刚调好的排版参数清空。
        """
        if roll is None:
            return
        cfg = roll.to_config()

        from processor.config_schema import sanitize_config
        merged = sanitize_config({**{k: v.get() if hasattr(v, 'get') else v
                                    for k, v in self.vars.items()},
                                  **{k: v for k, v in cfg.items() if v not in ("", None)}})

        for key, val in merged.items():
            var = self.vars.get(key)
            if var is None:
                continue
            try:
                if isinstance(var, tk.BooleanVar):
                    var.set(bool(val))
                elif isinstance(var, tk.IntVar):
                    var.set(int(val))
                else:
                    var.set(str(val))
            except (tk.TclError, ValueError, TypeError):
                pass        # 单个字段类型不符不该中断整轮回灌

        # 派生控件要跟着刷一遍，否则下拉框还停在旧值
        if hasattr(self, "sub_combo"):
            try:
                self.toggle_sub_format()
            except tk.TclError:
                pass
        try:
            self.update_ratio_label()
            self._update_info_labels()
            self.refresh_pack_combo()
        except tk.TclError:
            pass

        self._show_page(0)
        self.set_status(f"已载入「{roll.title}」的参数，可直接开始生成", "success")

    def cancel_process(self):
        if self.processor:
            self.processor.cancel()
            self.set_status("取消中...", "warning")

    # ---------------- 主题相关 ----------------

    def _next_theme_name(self):
        return "light" if self.colors["name"] == "深色" else "dark"

    def toggle_theme(self):
        key = self._next_theme_name()
        self.colors = THEMES[key]
        cfg = load_config()
        cfg["ui_theme"] = key
        save_config(cfg)
        self._apply_theme(self.colors)
        self.set_status(f"已切换 {self.colors['name']} 主题", "neutral")

    def _apply_theme(self, p):
        """按调色板 p 应用样式：ttk 控件（clam 基准）+ 自绘控件 + 字体层级。"""
        style = ttk.Style(self.root)
        try:
            style.theme_use('clam')
        except tk.TclError:
            pass

        # 字体层级：页面标题 > 卡片标题 > 正文 > 次要说明
        self.fonts = self._build_fonts(p)
        fonts = self.fonts

        # ---- 窗口与容器底色 ----
        self.root.configure(bg=p["bg"])
        self.canvas.configure(bg=p["bg"], highlightbackground=p["bg"])
        self.pages_host.configure(bg=p["bg"])
        self.nav_bar.configure(bg=p["bg"])
        self.action_bar.configure(bg=p["bg"])
        self.status_lbl.master.configure(bg=p["bg"])
        # tk 级控件不继承父级 bg，切主题必须显式同步（否则深色下会残留浅色方块）
        for wrapper in getattr(self, "_pages", ()):
            wrapper.configure(bg=p["bg"])

        style.configure("TFrame", background=p["bg"])

        # 底部操作栏：1px hairline 分隔线，制造 macOS 的层次
        if not getattr(self, "_action_bar_divider", None):
            self._action_bar_divider = tk.Frame(self.action_bar, height=1,
                                                bg=p["border"], highlightthickness=0)
            self._action_bar_divider.pack(fill=tk.X, side=tk.TOP, pady=(0, 9),
                                          before=self.btn_row)
        else:
            self._action_bar_divider.configure(bg=p["border"])
        style.configure("TLabel", background=p["bg"], foreground=p["fg"], font=fonts["body"])
        style.configure("Muted.TLabel", background=p["bg"], foreground=p["fg_muted"],
                        font=fonts["caption"])
        # 卡片内的标签必须用 surface 而不是 bg —— 卡片底板是 surface 色，
        # 而 TLabel 默认跟着页面底色走，于是文字后面会衬出一块深色矩形
        # （深色主题下尤其刺眼）。TCheckbutton/TRadiobutton 早已用 surface，
        # 这里把 Label 补齐成一致。
        style.configure("Card.TLabel", background=p["surface"], foreground=p["fg"],
                        font=fonts["body"])
        style.configure("CardMuted.TLabel", background=p["surface"],
                        foreground=p["fg_muted"], font=fonts["caption"])
        style.configure("CardTitle.TLabel", background=p["surface"],
                        foreground=p["fg"], font=fonts["card_title"])
        # 卡片内嵌套的 ttk.Frame —— TFrame 是页面底色，嵌套层需要 surface
        style.configure("Card.TFrame", background=p["surface"])
        style.configure("FieldLabel.TLabel", background=p["surface"], foreground=p["fg"],
                        font=fonts["field_label"])
        style.configure("TCheckbutton", background=p["surface"], foreground=p["fg"],
                        font=fonts["body"], indicatorbackground=p["field"],
                        indicatorforeground=p["accent_fg"], focuscolor=p["accent"])
        style.configure("TRadiobutton", background=p["surface"], foreground=p["fg"],
                        font=fonts["body"], indicatorbackground=p["field"],
                        indicatorforeground=p["accent_fg"], focuscolor=p["accent"])
        style.map("TCheckbutton",
                  background=[("active", p["surface"])],
                  foreground=[("disabled", p["disabled"])],
                  indicatorbackground=[("disabled", p["border"]), ("selected", p["accent"]),
                                       ("!selected", p["field"])])
        style.map("TRadiobutton",
                  background=[("active", p["surface"])],
                  foreground=[("disabled", p["disabled"])],
                  indicatorbackground=[("disabled", p["border"]), ("selected", p["accent"]),
                                       ("!selected", p["field"])])

        # 输入类控件（更柔和的浅描边 + 输入光标 + 选中态）
        entry_opts = dict(fieldbackground=p["field"], foreground=p["fg"],
                          bordercolor=p["border"], lightcolor=p["border"], darkcolor=p["border"],
                          insertcolor=p["fg"], arrowcolor=p["fg_muted"], font=fonts["body"],
                          focuscolor=p["accent"])
        style.configure("TEntry", padding=(7, 6), background=p["surface"], **entry_opts)
        style.configure("TCombobox", padding=(7, 6), arrowsize=13,
                        background=p["field"], **entry_opts)
        style.map("TCombobox",
                  fieldbackground=[("readonly", p["field"])],
                  background=[("readonly", p["field"]), ("active", p["hover"])],
                  arrowcolor=[("active", p["accent"])],
                  foreground=[("disabled", p["disabled"])],
                  selectbackground=[("readonly", p["active"])],
                  selectforeground=[("readonly", p["fg"])])
        style.configure("TSpinbox", padding=(7, 6), arrowsize=12, **entry_opts)

        # 卡片内的次级按钮（浏览 / 加载 / 保存 / 删除 …）
        # 底色要比卡片面略深，否则白底卡片上的按钮会「消失」，只剩文字
        style.configure("TButton", background=p["hover"], foreground=p["fg"],
                        bordercolor=p["border"], padding=(13, 6), font=fonts["body"],
                        relief="flat", focusthickness=0, focuscolor=p["accent"])
        style.map("TButton",
                  background=[("active", p["active"]), ("pressed", p["active"]),
                              ("disabled", p["bg"])],
                  foreground=[("active", p["fg"]), ("disabled", p["disabled"])],
                  bordercolor=[("active", p["accent"]), ("disabled", p["border"])])

        # 滑杆：主窗口已换成自绘 RoundedScale，这里不再需要 ttk 的 Scale 样式

        # 进度条 / ttk 滚动条（主滚动区已换成自绘，这里只服务预览子窗口）
        style.configure("Horizontal.TProgressbar", background=p["accent"],
                        troughcolor=p["border"], bordercolor=p["border"], lightcolor=p["accent"],
                        darkcolor=p["accent"])
        style.configure("Vertical.TScrollbar", background=p["border"],
                        troughcolor=p["bg"], bordercolor=p["bg"], arrowcolor=p["fg_muted"])
        style.configure("Horizontal.TScrollbar", background=p["border"],
                        troughcolor=p["bg"], bordercolor=p["bg"], arrowcolor=p["fg_muted"])
        style.configure("TSeparator", background=p["border"])

        # ---- 自绘控件统一换肤（卡片 / 药丸按钮 / 分段导航 / 滚动条） ----
        # 注意：这里不要用 except TclError: pass 静默吞掉 ——
        # 卡片换肤曾因为 master.cget('bg') 抛 TclError 被这样吞掉，
        # 结果切深色后 11 张卡片全部留在浅色，而日志里毫无痕迹。
        # 真有控件在换肤时抛错就该让它炸出来，否则界面就是「花」的。
        # 但已被 destroy 的控件要从名单里剔除：它们换肤必然抛
        # "invalid command name"，那是生命周期问题，不是样式问题。
        self._themed = [w for w in getattr(self, "_themed", ())
                        if self._widget_alive(w)]
        for w in self._themed:
            w.apply_theme(p, fonts)

        # ---- 状态栏与主题切换按钮 ----
        self.status_lbl.configure(bg=p["bg"], font=fonts["caption"])
        self.theme_btn.icon = "moon" if p["name"] == "深色" else "sun"
        self.theme_btn.set_text(p["name"])
        self.set_status(self.status_lbl.cget("text"),
                        getattr(self, "_status_kind", "neutral"))

        # 顶部导航与滚动条重绘
        if getattr(self, "nav", None) is not None:
            self.nav.set_index(self._current_page)

        # 归档库页自绘卡片与详情面板需按新调色板重建
        try:
            self.archive_page.apply_theme(p, fonts)
        except Exception:
            pass

        # 标题栏是 DWM 画的，必须单独跟上（否则深色下标题栏仍是白的）
        self._apply_titlebar_theme(p["name"] == "深色")

    def set_status(self, text, kind="neutral"):
        """主题感知的状态栏文本。kind: neutral / success / warning / error"""
        color = {
            "neutral": self.colors["fg_muted"],
            "success": self.colors["success"],
            "warning": self.colors["warning"],
            "error": self.colors["error"],
        }[kind]
        self._status_kind = kind
        self.status_lbl.config(text=text, foreground=color, bg=self.colors["bg"])
