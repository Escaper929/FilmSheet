# -*- coding: utf-8 -*-

import os
import sys
import threading
import json
import tkinter as tk
from tkinter import filedialog, messagebox, ttk
from filmsheet._version import __VERSION__
from utils.helpers import load_config, save_config, add_pack_image_history, LABEL_MAP, INFO_LAYOUT, NO_COLON_FIELDS, FILM_FORMAT_RATIOS

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
        self.root.geometry("720x780")
        self.root.minsize(560, 600)
        # 允许用户缩放窗口（内容区有滚动条兜底）

        cfg = load_config()
        self.pack_history = cfg.get("pack_images", [])

        # 确保子画幅有默认值
        sub_format = cfg.get("sub_format", "标准 36×24")
        if sub_format not in ["标准 36×24", "半格 18×24", "方形 24×24", "XPan 65×24", "645", "66", "67", "68", "69", "612", "617", "624"]:
            sub_format = "标准 36×24"

        self.vars = {
            'input_folder': tk.StringVar(),
            'output_file': tk.StringVar(value="filmsheet_output.jpg"),
            'thumb_width': tk.IntVar(value=400),
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
        self.font = _pick_font(root)          # 主字体（微软雅黑）
        self._font_widgets = []               # 需随主题更新字体的控件
        self._pages = []                      # 页面 wrapper 列表
        self._current_page = 0
        # 从配置恢复界面主题（默认浅色）
        cfg = load_config()
        self.colors = THEMES.get(cfg.get("ui_theme", "light"), THEMES["light"])
        self.build_ui()
        self._apply_theme(self.colors)

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
        action_bar = ttk.Frame(outer, padding=(14, 8, 14, 12))
        self.action_bar = action_bar
        action_bar.pack(side=tk.BOTTOM, fill=tk.X)

        self.progress_bar = ttk.Progressbar(action_bar, orient=tk.HORIZONTAL, mode='determinate')
        self.progress_bar.pack(fill=tk.X, pady=(0, 10))

        btn_row = ttk.Frame(action_bar)
        btn_row.pack(fill=tk.X)
        self.status_lbl = ttk.Label(btn_row, style="Muted.TLabel", text="FilmSheet Ready")
        self.status_lbl.pack(side=tk.LEFT)
        self.theme_btn = ttk.Button(btn_row, style="Toolbutton", width=14, command=self.toggle_theme)
        self.theme_btn.pack(side=tk.LEFT, padx=(10, 0))
        self.preview_btn = ttk.Button(btn_row, text="预览", command=self.preview_process)
        self.preview_btn.pack(side=tk.RIGHT, padx=(6, 0))
        self.cancel_btn = ttk.Button(btn_row, text="取消", command=self.cancel_process, state=tk.DISABLED)
        self.cancel_btn.pack(side=tk.RIGHT, padx=6)
        self.start_btn = ttk.Button(btn_row, text="开始生成", command=self.start_process, style="Accent.TButton", width=12)
        self.start_btn.pack(side=tk.RIGHT, padx=6)

        # ================= 顶部导航条（自绘，macOS 风格选中下划线） =================
        nav_bar = tk.Frame(outer, bg=self.colors["bg"])
        self.nav_bar = nav_bar
        nav_bar.pack(side=tk.TOP, fill=tk.X, padx=(8, 8), pady=(6, 0))

        self.nav_items = []          # [(label, index, underline_frame)]

        # ================= 可滚动内容区（页面） =================
        canvas = tk.Canvas(outer, highlightthickness=0)
        self.canvas = canvas
        scrollbar = ttk.Scrollbar(outer, orient=tk.VERTICAL, command=canvas.yview)
        canvas.configure(yscrollcommand=scrollbar.set,
                         bg=self.colors["bg"], highlightbackground=self.colors["bg"])

        scrollbar.pack(side=tk.RIGHT, fill=tk.Y)
        canvas.pack(side=tk.TOP, fill=tk.BOTH, expand=True)

        # 页面容器：各 tab 作为其子 Frame，用 pack 切换显示
        self.pages_host = tk.Frame(canvas, bg=self.colors["bg"])
        canvas.create_window((0, 0), window=self.pages_host, anchor="nw")

        def configure_scroll_region(event):
            canvas.configure(scrollregion=canvas.bbox("all"))
        self.pages_host.bind("<Configure>", configure_scroll_region)

        def _on_mousewheel(event):
            canvas.yview_scroll(int(-1*(event.delta/120)), "units")
        canvas.bind_all("<MouseWheel>", _on_mousewheel)

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

        basic_frame = ttk.LabelFrame(io_tab, text="输入", padding=12)
        basic_frame.grid(row=0, column=0, sticky=tk.NSEW, padx=(0, 6), pady=6)
        basic_frame.columnconfigure(1, weight=1)
        ttk.Label(basic_frame, text="图片来源").grid(row=0, column=0, sticky=tk.W, pady=4)
        ttk.Entry(basic_frame, textvariable=self.vars['input_folder'], width=30).grid(row=0, column=1, sticky=tk.EW, padx=8)
        ttk.Button(basic_frame, text="浏览…", command=self.browse_input).grid(row=0, column=2, padx=4)

        # Single photo export mode — shown first so users choose input method immediately
        self.single_photo_cb = ttk.Checkbutton(
            basic_frame, text="单张照片导出", variable=self.vars['single_photo_mode'])
        self.single_photo_cb.grid(row=1, column=0, columnspan=3, sticky=tk.W, pady=6)
        ttk.Checkbutton(
            basic_frame, text="附带 包装/信息/水印",
            variable=self.vars['single_photo_show_extra']).grid(row=2, column=0, columnspan=3, sticky=tk.W, pady=2)

        ttk.Separator(basic_frame, orient=tk.HORIZONTAL).grid(row=3, column=0, columnspan=3, sticky=tk.EW, pady=8)
        ttk.Label(basic_frame, text="输出").grid(row=4, column=0, sticky=tk.W, pady=4)
        ttk.Entry(basic_frame, textvariable=self.vars['output_file'], width=30).grid(row=4, column=1, columnspan=2, sticky=tk.EW, padx=8)

        out_frame = ttk.LabelFrame(io_tab, text="输出选项", padding=12)
        out_frame.grid(row=0, column=1, sticky=tk.NSEW, padx=(6, 0), pady=6)
        out_frame.columnconfigure(1, weight=1)

        ttk.Label(out_frame, text="格式").grid(row=0, column=0, sticky=tk.W, pady=4)
        fmt_combo = ttk.Combobox(out_frame, textvariable=self.vars['output_format'],
                                 values=["PNG", "JPG"], state="readonly", width=8)
        fmt_combo.grid(row=0, column=1, sticky=tk.W, padx=8)
        fmt_combo.bind("<<ComboboxSelected>>", self.update_ext)

        self.q_label = ttk.Label(out_frame, text="质量")
        self.q_label.grid(row=1, column=0, sticky=tk.W, pady=4)
        self.q_scale = ttk.Scale(out_frame, from_=1, to=100, variable=self.vars['quality'],
                                 orient=tk.HORIZONTAL, length=120)
        self.q_scale.grid(row=1, column=1, sticky=tk.EW, padx=8)
        self.q_val = ttk.Label(out_frame, textvariable=self.vars['quality'], width=3)
        self.q_val.grid(row=1, column=2, sticky=tk.W)
        self.update_ext(None)

        self.batch_cb = ttk.Checkbutton(out_frame, text="同时生成接触印相版", variable=self.vars['batch_export_enabled'])
        self.batch_cb.grid(row=2, column=0, columnspan=3, sticky=tk.W, pady=8)

        # =====================================================================
        # Tab 2: 画幅与排版
        # =====================================================================
        frame_tab = self._add_page("画幅与排版")
        frame_tab.columnconfigure(0, weight=1)
        frame_tab.columnconfigure(1, weight=1)

        mode_frame = ttk.LabelFrame(frame_tab, text="成像模式", padding=12)
        mode_frame.grid(row=0, column=0, sticky=tk.NSEW, padx=(0, 6), pady=6)
        ttk.Radiobutton(mode_frame, text="正片", variable=self.vars['processing_mode'], value="positive").pack(anchor=tk.W, pady=3)
        ttk.Radiobutton(mode_frame, text="负片", variable=self.vars['processing_mode'], value="negative").pack(anchor=tk.W, pady=3)

        sim_frame = ttk.LabelFrame(frame_tab, text="胶片模拟", padding=12)
        sim_frame.grid(row=0, column=1, sticky=tk.NSEW, padx=(6, 0), pady=6)
        sim_frame.columnconfigure(1, weight=1)
        ttk.Label(sim_frame, text="齿孔模式").grid(row=0, column=0, sticky=tk.W, pady=4)
        ttk.Combobox(sim_frame, textvariable=self.vars['perf_mode'],
                     values=["Auto", "KS (民用)", "BH (电影)"], state="readonly", width=13).grid(row=0, column=1, sticky=tk.W, padx=8)
        ttk.Label(sim_frame, text="渲染风格").grid(row=1, column=0, sticky=tk.W, pady=6)
        style_radio = ttk.Frame(sim_frame)
        style_radio.grid(row=1, column=1, sticky=tk.W)
        ttk.Radiobutton(style_radio, text="灯板正片", variable=self.vars['render_style'],
                        value="lightbox", command=self._on_style_changed).pack(side=tk.LEFT, pady=1)
        ttk.Radiobutton(style_radio, text="接触印相", variable=self.vars['render_style'],
                        value="contact_sheet", command=self._on_style_changed).pack(side=tk.LEFT, padx=(12,0))

        film_frame = ttk.LabelFrame(frame_tab, text="画幅", padding=12)
        film_frame.grid(row=1, column=0, sticky=tk.NSEW, padx=(0, 6), pady=6)
        film_frame.columnconfigure(1, weight=1)
        rf = ttk.Frame(film_frame)
        rf.grid(row=0, column=0, sticky=tk.W)
        ttk.Radiobutton(rf, text="135", variable=self.vars['film_format'], value="135",
                        command=self.toggle_sub_format).pack(side=tk.LEFT)
        ttk.Radiobutton(rf, text="120", variable=self.vars['film_format'], value="120",
                        command=self.toggle_sub_format).pack(side=tk.LEFT, padx=(12,0))
        ttk.Label(film_frame, text="比例").grid(row=0, column=2, sticky=tk.W, padx=(10,0))
        self.ratio_label = ttk.Label(film_frame, text="3:2")
        self.ratio_label.grid(row=0, column=3, sticky=tk.W)

        ttk.Label(film_frame, text="子画幅").grid(row=1, column=0, sticky=tk.W, pady=8)
        self.sub_combo = ttk.Combobox(film_frame, textvariable=self.vars['sub_format'],
                                      state="readonly", width=14)
        self.sub_combo.grid(row=1, column=1, sticky=tk.W, padx=8)
        self.sub_combo.bind("<<ComboboxSelected>>", lambda e: (self.update_ratio_label(), self.auto_adjust_columns()))

        # 初始化子画幅选项（避免首次点击画幅前下拉框为空）
        self.toggle_sub_format()

        layout_frame = ttk.LabelFrame(frame_tab, text="排版", padding=12)
        layout_frame.grid(row=1, column=1, sticky=tk.NSEW, padx=(6, 0), pady=6)
        layout_frame.columnconfigure(1, weight=1)
        ttk.Label(layout_frame, text="缩略图宽").grid(row=0, column=0, sticky=tk.W, pady=4)
        ttk.Spinbox(layout_frame, from_=300, to=1600, textvariable=self.vars['thumb_width'], width=8).grid(row=0, column=1, sticky=tk.W, padx=8)
        ttk.Label(layout_frame, text="每行列数").grid(row=1, column=0, sticky=tk.W, pady=4)
        ttk.Spinbox(layout_frame, from_=1, to=99, textvariable=self.vars['columns'], width=8).grid(row=1, column=1, sticky=tk.W, padx=8)
        ttk.Button(layout_frame, text="自适应画幅", command=self.auto_adjust_columns).grid(row=2, column=0, columnspan=2, sticky=tk.EW, pady=8)
        ttk.Checkbutton(layout_frame, text="强制横向", variable=self.vars['force_landscape']).grid(row=3, column=0, columnspan=2, sticky=tk.W)

        # =====================================================================
        # Tab 3: 外观
        # =====================================================================
        look_tab = self._add_page("外观")
        look_tab.columnconfigure(0, weight=1)
        look_tab.columnconfigure(1, weight=1)

        edge_frame = ttk.LabelFrame(look_tab, text="边字设置", padding=12)
        edge_frame.grid(row=0, column=0, sticky=tk.NSEW, padx=(0, 6), pady=6)
        edge_frame.columnconfigure(1, weight=1)
        ttk.Label(edge_frame, text="自定义内容").grid(row=0, column=0, sticky=tk.W)
        ttk.Entry(edge_frame, textvariable=self.vars['edge_text'], width=22).grid(row=0, column=1, sticky=tk.EW, padx=8)
        ttk.Label(edge_frame, text="(留空则从'胶卷'字段自动生成)", style="Muted.TLabel").grid(row=1, column=0, columnspan=2, sticky=tk.W, pady=(6,0))

        sig_frame = ttk.LabelFrame(look_tab, text="水印签名", padding=12)
        sig_frame.grid(row=0, column=1, sticky=tk.NSEW, padx=(6, 0), pady=6)
        sig_frame.columnconfigure(1, weight=1)
        ttk.Label(sig_frame, text="签名").grid(row=0, column=0, sticky=tk.W)
        ttk.Entry(sig_frame, textvariable=self.vars['signature'], width=22).grid(row=0, column=1, sticky=tk.EW, padx=8)
        ttk.Label(sig_frame, text="(留空则不添加水印)", style="Muted.TLabel").grid(row=1, column=0, columnspan=2, sticky=tk.W, pady=(6,0))

        pack_frame = ttk.LabelFrame(look_tab, text="胶卷包装图", padding=12)
        pack_frame.grid(row=1, column=0, columnspan=2, sticky=tk.NSEW, pady=6)
        pack_frame.columnconfigure(1, weight=1)

        ttk.Label(pack_frame, text="图片").grid(row=0, column=0, sticky=tk.W)
        self.pack_combo = ttk.Combobox(pack_frame, textvariable=self.vars['pack_image'],
                                       state="readonly", width=24)
        self.pack_combo.grid(row=0, column=1, sticky=tk.EW, padx=8)
        self.pack_combo.bind("<<ComboboxSelected>>", self.on_pack_combo_change)
        ttk.Button(pack_frame, text="浏览", command=self.browse_pack_image).grid(row=0, column=2, padx=4)
        ttk.Button(pack_frame, text="清除", command=self.clear_pack_image).grid(row=0, column=3, padx=4)

        ttk.Label(pack_frame, text="位置").grid(row=1, column=0, sticky=tk.W, pady=8)
        pos_combo = ttk.Combobox(pack_frame, textvariable=self.vars['pack_position'],
                                 values=["left", "right"], state="readonly", width=6)
        pos_combo.grid(row=1, column=1, sticky=tk.W, padx=8)
        pos_combo.bind("<<ComboboxSelected>>", self.save_pack_config)
        ttk.Checkbutton(pack_frame, text="描边", variable=self.vars['pack_border_stroke'],
                        command=self.save_pack_config).grid(row=1, column=2, sticky=tk.W, padx=8)

        ttk.Label(pack_frame, text="大小").grid(row=2, column=0, sticky=tk.W, pady=4)
        pack_size_scale = ttk.Scale(pack_frame, from_=10, to=100, variable=self.vars['pack_size'],
                                    orient=tk.HORIZONTAL, length=140, command=self.save_pack_config)
        pack_size_scale.grid(row=2, column=1, sticky=tk.EW, padx=8)
        ttk.Label(pack_frame, textvariable=self.vars['pack_size'], width=4).grid(row=2, column=2, sticky=tk.W)
        ttk.Label(pack_frame, text="%", style="Muted.TLabel").grid(row=2, column=3, sticky=tk.W)

        self.refresh_pack_combo()

        # =====================================================================
        # Tab 4: 拍摄信息
        # =====================================================================
        info_tab = self._add_page("拍摄信息")

        tmpl_frame = ttk.LabelFrame(info_tab, text="模板管理", padding=12)
        tmpl_frame.grid(row=0, column=0, sticky=tk.EW, pady=6)
        ttk.Label(tmpl_frame, text="模板").grid(row=0, column=0, sticky=tk.W)
        self.tmpl_combo = ttk.Combobox(tmpl_frame, textvariable=self.vars['current_template'],
                                        state="readonly", width=20)
        self.tmpl_combo.grid(row=0, column=1, padx=8)
        self.tmpl_combo.bind("<<ComboboxSelected>>", self.load_template_from_combo)
        ttk.Button(tmpl_frame, text="加载", command=self.load_selected_template).grid(row=0, column=2, padx=3)
        ttk.Button(tmpl_frame, text="保存", command=self.save_new_template).grid(row=0, column=3, padx=3)
        ttk.Button(tmpl_frame, text="删除", command=self.delete_selected_template).grid(row=0, column=4, padx=3)

        info_frame = ttk.LabelFrame(info_tab, text="拍摄信息记录 (选填)", padding=12)
        info_frame.grid(row=1, column=0, sticky=tk.EW, pady=6)
        info_frame.columnconfigure(1, weight=1)
        info_frame.columnconfigure(3, weight=1)
        info_frame.columnconfigure(5, weight=1)

        lang_frame = ttk.Frame(info_frame)
        lang_frame.grid(row=0, column=0, columnspan=6, sticky=tk.W, pady=(0,8))
        ttk.Label(lang_frame, text="标签语言").pack(side=tk.LEFT, padx=(0,8))
        lang_combo = ttk.Combobox(lang_frame, textvariable=self.vars['info_lang'],
                                  values=["zh", "en"], state="readonly", width=6)
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
            for key, r, c in row_items:
                if key is None:
                    continue
                lbl = ttk.Label(info_frame, style="FieldLabel.TLabel", text=self._get_label_text(key))
                lbl.grid(row=r, column=c, sticky=tk.W, padx=5, pady=2)
                self.info_labels[key] = lbl
                entry_w = 10 if key == 'roll' else 14
                hist_key = f"history_{key}"
                combo_history = getattr(self, hist_key, [])
                combo = ttk.Combobox(info_frame, textvariable=self.vars[f'info_{key}'],
                                     values=combo_history, state="normal", width=entry_w)
                combo.grid(row=r, column=c+1, sticky=tk.EW, padx=5, pady=2)
                current_val = self.vars[f'info_{key}'].get()
                combo.set(current_val)
                combo.bind("<Return>", lambda e, k=key: self.update_field_history(k))

        # 收尾：刷新可选控件
        self._refresh_tmpl_combo()
        self._update_batch_checkbox_label()
        self.auto_adjust_columns()
        # 默认显示第一个页面并构建导航（macOS 风格下划线）
        self._show_page(0)

    def _add_page(self, title):
        """创建一个页面 + 对应导航标签；返回供填充的内部 frame。"""
        p = self.colors
        wrapper = tk.Frame(self.pages_host, bg=p["bg"])
        page = ttk.Frame(wrapper, padding="14")
        page.pack(fill=tk.BOTH, expand=True)

        # 导航标签（单个词，可点击）
        idx = len(self.nav_items)
        lbl = tk.Label(self.nav_bar, text=title, cursor="hand2",
                       bg=p["bg"], fg=p["fg_muted"],
                       font=(self.font, 9, "bold"))
        lbl.pack(side=tk.LEFT, padx=(0, 4), pady=(0, 0))
        lbl.bind("<Button-1>", lambda e, i=idx: self._show_page(i))
        # hover
        lbl.bind("<Enter>", lambda e, i=idx: self._nav_hover(i, True))
        lbl.bind("<Leave>", lambda e, i=idx: self._nav_hover(i, False))

        self._pages.append(wrapper)
        self.nav_items.append(lbl)
        return page

    def _nav_hover(self, idx, hover):
        active = self._current_page == idx
        if hover and not active:
            self.nav_items[idx].configure(fg=self.colors["fg"])
        elif not active:
            self.nav_items[idx].configure(fg=self.colors["fg_muted"])

    def _show_page(self, idx):
        self._current_page = idx
        for i, wrapper in enumerate(self._pages):
            if i == idx:
                wrapper.pack(fill=tk.BOTH, expand=True)
            else:
                wrapper.pack_forget()
        # 更新导航态
        for i, lbl in enumerate(self.nav_items):
            if i == idx:
                lbl.configure(fg=self.colors["accent"])
            else:
                lbl.configure(fg=self.colors["fg_muted"])
        # 蓝色下划线移到选中标签下方
        if not getattr(self, "_nav_underline", None):
            self._nav_underline = tk.Frame(self.nav_bar, height=2,
                                           bg=self.colors["accent"], highlightthickness=0)
        else:
            self._nav_underline.configure(bg=self.colors["accent"])
        # nav_bar 需要 exp 更新
        self.nav_bar.update_idletasks()
        x = self.nav_items[idx].winfo_x()
        w = self.nav_items[idx].winfo_width()
        y = self.nav_items[idx].winfo_y() + self.nav_items[idx].winfo_height()
        self._nav_underline.place(x=x, y=y + 2, width=w, height=2)
        self._nav_underline.lift()

    def toggle_sub_format(self):
        """切换画幅时更新子画幅选项和比例显示"""
        film_format = self.vars['film_format'].get()
        if film_format == "135":
            self.sub_combo['values'] = ["标准 36×24", "半格 18×24", "方形 24×24", "XPan 65×24"]
            current = self.vars['sub_format'].get()
            if current not in ["标准 36×24", "半格 18×24", "方形 24×24", "XPan 65×24"]:
                self.vars['sub_format'].set("标准 36×24")
        else:
            self.sub_combo['values'] = ["645", "66", "67", "68", "69", "612", "617", "624"]
            current = self.vars['sub_format'].get()
            if current not in ["645", "66", "67", "68", "69", "612", "617", "624"]:
                self.vars['sub_format'].set("66")
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

        self.start_btn.config(state=tk.DISABLED)
        self.cancel_btn.config(state=tk.NORMAL)
        self.progress_bar['value'] = 0

        # Import the image-processing stack only once the user starts work.
        from processor.film_processor import FilmProcessor
        self.processor = FilmProcessor(config)
        threading.Thread(target=self.run_worker, daemon=True).start()

    def run_worker(self):
        upd_s = lambda m: self.root.after(0, lambda: self.status_lbl.config(text=m))
        upd_p = lambda v, m: self.root.after(0, lambda: [self.progress_bar.config(value=v), self.status_lbl.config(text=m)])
        result = self.processor.run(upd_s, upd_p)
        self.root.after(0, self.process_finished, result)

    def process_finished(self, result):
        self.start_btn.config(state=tk.NORMAL)
        self.cancel_btn.config(state=tk.DISABLED)

        if result == "success":
            self.progress_bar['value'] = 100
            self.set_status("FilmSheet Done!", "success")
            messagebox.showinfo("Success", f"文件已保存至：\n{self.processor.config['output_path']}")
        elif result == "已取消":
            self.set_status("已取消", "warning")
        else:
            self.set_status("失败", "error")
            messagebox.showerror("Error", result)

    def cancel_process(self):
        if self.processor:
            self.processor.cancel()
            self.status_lbl.config(text="取消中...", foreground=self.colors["warning"])

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
        """按调色板 p 应用 ttk 样式（基于 clam，扁平可全面定制）。"""
        style = ttk.Style(self.root)
        try:
            style.theme_use('clam')
        except tk.TclError:
            pass

        # 窗口与滚动画布底色
        self.root.configure(bg=p["bg"])
        self.canvas.configure(bg=p["bg"])
        self.pages_host.configure(bg=p["bg"])
        self.nav_bar.configure(bg=p["bg"])

        # 基础容器/文字 —— 统一字体，减少方框感
        F = self.font                      # 主字体族（微软雅黑等）
        f_big   = (F, 10, "bold")          # 卡片标题
        f_body  = (F, 9)                    # 正文
        f_label = (F, 9, "bold")            # 字段标签
        f_small = (F, 8,)                   # 次要说明

        style.configure("TFrame", background=p["bg"])
        # 底部操作栏：加 1px 顶部 hairline 分隔线，制造 macOS 的分离层次
        if not getattr(self, "_action_bar_divider", None):
            self._action_bar_divider = tk.Frame(self.action_bar, height=1,
                                                bg=p["border"], highlightthickness=0)
            self._action_bar_divider.pack(fill=tk.X, side=tk.TOP, pady=(0, 10), before=self.progress_bar)
        else:
            self._action_bar_divider.configure(bg=p["border"])
        style.configure("TLabelframe", background=p["surface"], bordercolor=p["border"],
                        relief="flat", borderwidth=1)
        style.configure("TLabelframe.Label", background=p["surface"], foreground=p["fg"],
                        font=f_big)
        style.configure("TLabel", background=p["bg"], foreground=p["fg"], font=f_body)
        style.configure("FieldLabel.TLabel", background=p["surface"], foreground=p["fg"], font=f_label)
        style.configure("Muted.TLabel", background=p["bg"], foreground=p["fg_muted"], font=f_small)
        style.configure("CardTitle.TLabel", background=p["surface"], foreground=p["accent"],
                        font=f_label)
        style.configure("TCheckbutton", background=p["surface"], foreground=p["fg"], font=f_body)
        style.configure("TRadiobutton", background=p["surface"], foreground=p["fg"], font=f_body)
        style.map("TCheckbutton", background=[("active", p["surface"])],
                  foreground=[("disabled", p["disabled"])],
                  indicatorbackground=[("!disabled", p["field"])])
        style.map("TRadiobutton", background=[("active", p["surface"])],
                  foreground=[("disabled", p["disabled"])],
                  indicatorbackground=[("!disabled", p["field"])])

        # 输入类控件（更柔和的浅描边 + 输入光标 + 选中态）
        entry_opts = dict(fieldbackground=p["field"], foreground=p["fg"],
                          bordercolor=p["border"], lightcolor=p["border"], darkcolor=p["border"],
                          insertcolor=p["fg"], arrowcolor=p["fg_muted"], font=f_body,
                          focuscolor=p["accent"])
        style.configure("TEntry", padding=(6, 5), background=p["surface"], **entry_opts)
        style.configure("TCombobox", padding=(6, 5), **entry_opts)
        style.map("TCombobox", fieldbackground=[("readonly", p["field"])],
                  foreground=[("disabled", p["disabled"])],
                  selectbackground=[("readonly", p["active"])],
                  selectforeground=[("readonly", p["fg"])])
        style.configure("TSpinbox", padding=(6, 5), **entry_opts)

        # 按钮 —— 更圆润柔和（加大内边距、减小边框感）
        style.configure("TButton", background=p["surface"], foreground=p["fg"],
                        bordercolor=p["border"], padding=(14, 7), font=f_body,
                        relief="flat", focusthickness=0, focuscolor=p["accent"])
        style.map("TButton",
                  background=[("active", p["hover"]), ("pressed", p["active"]), ("disabled", p["bg"])],
                  foreground=[("active", p["fg"]), ("disabled", p["disabled"])],
                  bordercolor=[("active", p["accent"]), ("disabled", p["border"])])
        style.configure("Accent.TButton", background=p["accent"], foreground=p["accent_fg"],
                        bordercolor=p["accent"], padding=(18, 8), font=(F, 9, "bold"),
                        relief="flat", focusthickness=0)
        style.map("Accent.TButton",
                  background=[("active", p["accent"]), ("pressed", p["active"]), ("disabled", p["bg"])],
                  foreground=[("disabled", p["disabled"])])
        style.configure("Toolbutton", background=p["bg"], foreground=p["fg_muted"],
                        bordercolor=p["bg"], padding=(10, 5), font=f_small, focusthickness=0)
        style.map("Toolbutton", background=[("active", p["hover"])],
                  foreground=[("active", p["fg"])])

        # 进度条 / 滚动条
        style.configure("Horizontal.TProgressbar", background=p["accent"],
                        troughcolor=p["border"], bordercolor=p["border"], lightcolor=p["accent"],
                        darkcolor=p["accent"])
        style.configure("Vertical.TScrollbar", background=p["border"],
                        troughcolor=p["bg"], bordercolor=p["bg"], arrowcolor=p["fg_muted"])
        style.configure("Horizontal.TScrollbar", background=p["border"],
                        troughcolor=p["bg"], bordercolor=p["bg"], arrowcolor=p["fg_muted"])

        # 标签页 —— 已改为自绘导航（见 _show_page），此样式不再使用
        # 分隔线
        style.configure("TSeparator", background=p["border"])

        # 切换按钮文案
        self.theme_btn.config(text=f"主题：{p['name']}")

        # 重绘自绘导航（macOS 下划线）
        if self.nav_items:
            self._show_page(self._current_page)

    def set_status(self, text, kind="neutral"):
        """主题感知的状态栏文本。kind: neutral / success / warning / error"""
        color = {
            "neutral": self.colors["fg_muted"],
            "success": self.colors["success"],
            "warning": self.colors["warning"],
            "error": self.colors["error"],
        }[kind]
        self.status_lbl.config(text=text, foreground=color)
