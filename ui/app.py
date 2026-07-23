# -*- coding: utf-8 -*-

import os
import sys
import threading
import tkinter as tk
from tkinter import filedialog, messagebox, ttk
from PIL import Image

from processor.film_processor import FilmProcessor
from utils.helpers import load_config, save_config, add_pack_image_history, LABEL_MAP, INFO_LAYOUT, NO_COLON_FIELDS, FILM_FORMAT_RATIOS

class App:
    def __init__(self, root):
        self.root = root
        self.root.title("FilmSheet v1.6.3 @Escaper")
        self.root.geometry("660x850")

        cfg = load_config()
        self.pack_history = cfg.get("pack_images", [])

        # 确保子画幅有默认值
        sub_format = cfg.get("sub_format", "标准 36×24")
        if sub_format not in ["标准 36×24", "半格 18×24", "方形 24×24", "XPan 65×24", "645", "66", "67", "68", "69", "612", "617"]:
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
        }

        self.vars['single_photo_mode'] = tk.BooleanVar(value=False)
        self.vars['single_image_path'] = tk.StringVar()

        for key in LABEL_MAP:
            self.vars[f'info_{key}'] = tk.StringVar()

        self.processor = None
        self.info_labels = {}
        self.build_ui()
        self._refresh_tmpl_combo()
        self._update_batch_checkbox_label()

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
        main_container = ttk.Frame(self.root)
        main_container.pack(fill=tk.BOTH, expand=True)

        canvas = tk.Canvas(main_container, highlightthickness=0)
        scrollbar = ttk.Scrollbar(main_container, orient=tk.VERTICAL, command=canvas.yview)
        canvas.configure(yscrollcommand=scrollbar.set)

        scrollbar.pack(side=tk.RIGHT, fill=tk.Y)
        canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

        main_frame = ttk.Frame(canvas, padding="10")
        canvas.create_window((0, 0), window=main_frame, anchor="nw")

        def configure_scroll_region(event):
            canvas.configure(scrollregion=canvas.bbox("all"))
        main_frame.bind("<Configure>", configure_scroll_region)

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

        # ---- 基本设置 ----
        basic_frame = ttk.LabelFrame(main_frame, text="基本设置", padding="10")
        basic_frame.grid(row=0, column=0, columnspan=4, sticky=tk.EW, pady=5)
        ttk.Label(basic_frame, text="图片来源:").grid(row=0, column=0, sticky=tk.W, pady=5)
        ttk.Entry(basic_frame, textvariable=self.vars['input_folder'], width=35).grid(row=0, column=1, padx=5)
        ttk.Button(basic_frame, text="浏览...", command=self.browse_input).grid(row=0, column=2, padx=5)
        ttk.Label(basic_frame, text="输出:").grid(row=1, column=0, sticky=tk.W, pady=5)
        ttk.Entry(basic_frame, textvariable=self.vars['output_file'], width=35).grid(row=1, column=1, columnspan=2, sticky=tk.EW)

        # Single photo export mode — shown first so users choose input method immediately
        self.single_photo_cb = ttk.Checkbutton(
            basic_frame, text="单张照片导出", variable=self.vars['single_photo_mode'])
        self.single_photo_cb.grid(row=2, column=0, columnspan=3, sticky=tk.W, pady=5)

        # ---- 参数设置 ----
        param_frame = ttk.LabelFrame(main_frame, text="参数设置", padding="10")
        param_frame.grid(row=1, column=0, columnspan=4, sticky=tk.EW, pady=5)

        # 第一行：成像模式 + 画幅 + 子画幅 + 比例
        ttk.Label(param_frame, text="成像模式:").grid(row=0, column=0, sticky=tk.W)
        mode_radio = ttk.Frame(param_frame)
        mode_radio.grid(row=0, column=1, sticky=tk.W)
        ttk.Radiobutton(mode_radio, text="正片", variable=self.vars['processing_mode'], value="positive").pack(side=tk.LEFT)
        ttk.Radiobutton(mode_radio, text="负片", variable=self.vars['processing_mode'], value="negative").pack(side=tk.LEFT, padx=(10,0))

        ttk.Label(param_frame, text="画幅:").grid(row=0, column=2, sticky=tk.W, padx=(20,0))
        rf = ttk.Frame(param_frame)
        rf.grid(row=0, column=3, sticky=tk.W)
        ttk.Radiobutton(rf, text="135", variable=self.vars['film_format'], value="135",
                        command=self.toggle_sub_format).pack(side=tk.LEFT)
        ttk.Radiobutton(rf, text="120", variable=self.vars['film_format'], value="120",
                        command=self.toggle_sub_format).pack(side=tk.LEFT, padx=(10,0))

        # 子画幅下拉框
        self.sub_combo = ttk.Combobox(param_frame, textvariable=self.vars['sub_format'],
                                      state="readonly", width=12)
        self.sub_combo.grid(row=0, column=4, sticky=tk.W, padx=5)
        self.sub_combo.bind("<<ComboboxSelected>>", lambda e: self.update_ratio_label())

        # 比例显示
        ttk.Label(param_frame, text="比例:").grid(row=0, column=5, sticky=tk.W, padx=(5,0))
        self.ratio_label = ttk.Label(param_frame, text="3:2", width=6)
        self.ratio_label.grid(row=0, column=6, sticky=tk.W)

        # 初始化子画幅选项（避免首次点击画幅前下拉框为空）
        self.toggle_sub_format()

        # 第二行：缩略图宽 + 每行列数 + 自适应按钮 + 强制横向
        ttk.Label(param_frame, text="缩略图宽:").grid(row=1, column=0, sticky=tk.W, pady=5)
        ttk.Spinbox(param_frame, from_=300, to=1600, textvariable=self.vars['thumb_width'], width=6).grid(row=1, column=1, sticky=tk.W)
        ttk.Label(param_frame, text="每行列数:").grid(row=1, column=2, sticky=tk.W, padx=(20,0))
        ttk.Spinbox(param_frame, from_=3, to=10, textvariable=self.vars['columns'], width=6).grid(row=1, column=3, sticky=tk.W)
        ttk.Button(param_frame, text="自适应画幅", command=self.auto_adjust_columns).grid(row=1, column=4, sticky=tk.W, padx=(10,0))
        ttk.Checkbutton(param_frame, text="强制横向", variable=self.vars['force_landscape']).grid(row=1, column=5, columnspan=2, sticky=tk.W, padx=(10,0))

        # 第三行：齿孔模式 + 渲染风格
        ttk.Label(param_frame, text="齿孔模式:").grid(row=2, column=0, sticky=tk.W, pady=5)
        ttk.Combobox(param_frame, textvariable=self.vars['perf_mode'],
                     values=["Auto", "KS (民用)", "BH (电影)"], state="readonly", width=10).grid(row=2, column=1, sticky=tk.W)

        ttk.Label(param_frame, text="渲染风格:").grid(row=2, column=2, sticky=tk.W, padx=(20,0))
        style_radio = ttk.Frame(param_frame)
        style_radio.grid(row=2, column=3, columnspan=3, sticky=tk.W)
        ttk.Radiobutton(style_radio, text="灯板正片", variable=self.vars['render_style'],
                        value="lightbox", command=self._on_style_changed).pack(side=tk.LEFT)
        ttk.Radiobutton(style_radio, text="接触印相", variable=self.vars['render_style'],
                        value="contact_sheet", command=self._on_style_changed).pack(side=tk.LEFT, padx=(10,0))

        # ---- 模板管理 ----
        tmpl_frame = ttk.LabelFrame(main_frame, text="模板管理", padding="5")
        tmpl_frame.grid(row=2, column=0, columnspan=4, sticky=tk.EW, pady=5)

        ttk.Label(tmpl_frame, text="模板:").grid(row=0, column=0, sticky=tk.W)
        self.tmpl_combo = ttk.Combobox(tmpl_frame, textvariable=self.vars['current_template'],
                                        state="readonly", width=18)
        self.tmpl_combo.grid(row=0, column=1, padx=5)
        self.tmpl_combo.bind("<<ComboboxSelected>>", self.load_template_from_combo)
        ttk.Button(tmpl_frame, text="加载", command=self.load_selected_template).grid(row=0, column=2, padx=2)
        ttk.Button(tmpl_frame, text="保存", command=self.save_new_template).grid(row=0, column=3, padx=2)
        ttk.Button(tmpl_frame, text="删除", command=self.delete_selected_template).grid(row=0, column=4, padx=2)

        # ---- 边字设置 ----
        edge_frame = ttk.LabelFrame(main_frame, text="边字设置", padding="5")
        edge_frame.grid(row=3, column=0, columnspan=4, sticky=tk.EW, pady=5)

        ttk.Label(edge_frame, text="自定义内容:").grid(row=0, column=0, sticky=tk.W)
        ttk.Entry(edge_frame, textvariable=self.vars['edge_text'], width=25).grid(row=0, column=1, sticky=tk.W, padx=(0,10))
        ttk.Label(edge_frame, text="(留空则从'胶卷'字段自动生成)", foreground="gray").grid(row=0, column=2, sticky=tk.W)

        # ---- 水印签名 ----
        sig_frame = ttk.LabelFrame(main_frame, text="水印签名", padding="5")
        sig_frame.grid(row=4, column=0, columnspan=4, sticky=tk.EW, pady=5)

        ttk.Label(sig_frame, text="签名:").grid(row=0, column=0, sticky=tk.W)
        ttk.Entry(sig_frame, textvariable=self.vars['signature'], width=25).grid(row=0, column=1, sticky=tk.W, padx=(0,10))
        ttk.Label(sig_frame, text="(留空则不添加水印)", foreground="gray").grid(row=0, column=2, sticky=tk.W)

        # ---- 胶卷包装图 ----
        pack_frame = ttk.LabelFrame(main_frame, text="胶卷包装图", padding="10")
        pack_frame.grid(row=5, column=0, columnspan=4, sticky=tk.EW, pady=5)

        ttk.Label(pack_frame, text="图片:").grid(row=0, column=0, sticky=tk.W)
        self.pack_combo = ttk.Combobox(pack_frame, textvariable=self.vars['pack_image'],
                                       state="readonly", width=25)
        self.pack_combo.grid(row=0, column=1, sticky=tk.EW, padx=5)
        self.pack_combo.bind("<<ComboboxSelected>>", self.on_pack_combo_change)
        ttk.Button(pack_frame, text="浏览", command=self.browse_pack_image).grid(row=0, column=2, padx=2)
        ttk.Button(pack_frame, text="清除", command=self.clear_pack_image).grid(row=0, column=3, padx=2)

        ttk.Label(pack_frame, text="位置:").grid(row=1, column=0, sticky=tk.W, pady=5)
        pos_combo = ttk.Combobox(pack_frame, textvariable=self.vars['pack_position'],
                                 values=["left", "right"], state="readonly", width=6)
        pos_combo.grid(row=1, column=1, sticky=tk.W)
        pos_combo.bind("<<ComboboxSelected>>", self.save_pack_config)
        ttk.Checkbutton(pack_frame, text="描边", variable=self.vars['pack_border_stroke'],
                        command=self.save_pack_config).grid(row=1, column=2, sticky=tk.W, padx=(10,0))

        # 包装图大小滑块
        ttk.Label(pack_frame, text="大小:").grid(row=2, column=0, sticky=tk.W, pady=5)
        pack_size_scale = ttk.Scale(pack_frame, from_=10, to=100, variable=self.vars['pack_size'],
                                    orient=tk.HORIZONTAL, length=120, command=self.save_pack_config)
        pack_size_scale.grid(row=2, column=1, sticky=tk.W, padx=5)
        ttk.Label(pack_frame, textvariable=self.vars['pack_size'], width=4).grid(row=2, column=2, sticky=tk.W)
        ttk.Label(pack_frame, text="%", foreground="gray").grid(row=2, column=3, sticky=tk.W)

        self.refresh_pack_combo()

        # ---- 拍摄信息 ----
        info_frame = ttk.LabelFrame(main_frame, text="拍摄信息记录 (选填)", padding="10")
        info_frame.grid(row=6, column=0, columnspan=4, sticky=tk.EW, pady=5)

        lang_frame = ttk.Frame(info_frame)
        lang_frame.grid(row=0, column=0, columnspan=8, sticky=tk.W, pady=(0,5))
        ttk.Label(lang_frame, text="标签语言:").pack(side=tk.LEFT, padx=(0,5))
        lang_combo = ttk.Combobox(lang_frame, textvariable=self.vars['info_lang'],
                                  values=["zh", "en"], state="readonly", width=6)
        lang_combo.pack(side=tk.LEFT)
        lang_combo.bind("<<ComboboxSelected>>", self._update_info_labels)

        gui_layout = [
            [('roll', 1, 0), ('camera', 1, 2), ('film', 1, 4)],
            [('shoot_date', 2, 0), ('dev_date', 2, 2), (None, 2, 4)],
            [('proc', 3, 0), ('lab', 3, 2), ('scanner', 3, 4)]
        ]
        for row_items in gui_layout:
            for key, r, c in row_items:
                if key is None:
                    continue
                lbl = ttk.Label(info_frame, text=self._get_label_text(key))
                lbl.grid(row=r, column=c, sticky=tk.W, padx=5, pady=2)
                self.info_labels[key] = lbl
                entry_w = 10 if key == 'roll' else 14
                ttk.Entry(info_frame, textvariable=self.vars[f'info_{key}'], width=entry_w).grid(
                    row=r, column=c+1, sticky=tk.EW, padx=5, pady=2
                )

        # ---- 输出选项 ----
        out_frame = ttk.LabelFrame(main_frame, text="输出选项", padding="10")
        out_frame.grid(row=7, column=0, columnspan=4, sticky=tk.EW, pady=5)

        ttk.Label(out_frame, text="格式:").grid(row=0, column=0, sticky=tk.W)
        fmt_combo = ttk.Combobox(out_frame, textvariable=self.vars['output_format'],
                                 values=["PNG", "JPG"], state="readonly", width=8)
        fmt_combo.grid(row=0, column=1, sticky=tk.W, padx=5)
        fmt_combo.bind("<<ComboboxSelected>>", self.update_ext)

        self.q_label = ttk.Label(out_frame, text="质量:")
        self.q_scale = ttk.Scale(out_frame, from_=1, to=100, variable=self.vars['quality'],
                                 orient=tk.HORIZONTAL, length=100)
        self.q_val = ttk.Label(out_frame, textvariable=self.vars['quality'], width=3)
        self.update_ext(None)

        # Batch export checkbox
        self.batch_cb = ttk.Checkbutton(out_frame, text="同时生成接触印相版", variable=self.vars['batch_export_enabled'])
        self.batch_cb.grid(row=0, column=5, sticky=tk.W, padx=(20,0))

        # ---- 控制按钮 ----
        ctrl_frame = ttk.Frame(main_frame)
        ctrl_frame.grid(row=8, column=0, columnspan=4, pady=15)

        self.progress_bar = ttk.Progressbar(ctrl_frame, orient=tk.HORIZONTAL, length=400, mode='determinate')
        self.progress_bar.pack(side=tk.TOP, fill=tk.X, pady=(0,10))

        btn_frame = ttk.Frame(ctrl_frame)
        btn_frame.pack(side=tk.BOTTOM)
        self.preview_btn = ttk.Button(btn_frame, text="预览", command=self.preview_process)
        self.preview_btn.pack(side=tk.LEFT, padx=5)
        self.start_btn = ttk.Button(btn_frame, text="开始生成", command=self.start_process)
        self.start_btn.pack(side=tk.LEFT, padx=5)
        self.cancel_btn = ttk.Button(btn_frame, text="取消", command=self.cancel_process, state=tk.DISABLED)
        self.cancel_btn.pack(side=tk.LEFT, padx=5)
        self.status_lbl = ttk.Label(main_frame, text="FilmSheet Ready", foreground="gray")
        self.status_lbl.grid(row=9, column=0, columnspan=4, pady=5)

    def toggle_sub_format(self):
        """切换画幅时更新子画幅选项和比例显示"""
        film_format = self.vars['film_format'].get()
        if film_format == "135":
            self.sub_combo['values'] = ["标准 36×24", "半格 18×24", "方形 24×24", "XPan 65×24"]
            current = self.vars['sub_format'].get()
            if current not in ["标准 36×24", "半格 18×24", "方形 24×24", "XPan 65×24"]:
                self.vars['sub_format'].set("标准 36×24")
        else:
            self.sub_combo['values'] = ["645", "66", "67", "68", "69", "612", "617"]
            current = self.vars['sub_format'].get()
            if current not in ["645", "66", "67", "68", "69", "612", "617"]:
                self.vars['sub_format'].set("66")
        self.update_ratio_label()

    # ---- 模板管理 ----

    TEMPLATE_SAVE_KEYS = [
        'film_format', 'sub_format', 'render_style', 'pack_image', 'pack_position',
        'pack_size', 'pack_border_stroke', 'processing_mode', 'thumb_width', 'columns',
        'spacing', 'force_landscape', 'perf_mode', 'output_format', 'quality',
        'signature', 'batch_export_enabled', 'edge_text',
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
        self.status_lbl.config(text=f"已加载模板: {name}", foreground="gray")

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
        self.status_lbl.config(text=f"已保存模板: {name}", foreground="gray")

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
            self.status_lbl.config(text=f"已删除模板: {name}", foreground="gray")

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
                "645": "1.25:1",
                "66": "1:1",
                "67": "1.167:1",
                "68": "1.333:1",
                "69": "1.5:1",
                "612": "2:1",
                "617": "2.833:1"
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
            if sub_format in ["645", "66"]:
                recommended = 6
            elif sub_format in ["67", "68"]:
                recommended = 5
            elif sub_format == "69":
                recommended = 4
            elif sub_format == "612":
                recommended = 4
            elif sub_format == "617":
                recommended = 3

        self.vars['columns'].set(recommended)
        self.status_lbl.config(text=f"自适应: {sub_format} → 每行 {recommended} 张", foreground="gray")

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

    def browse_input(self):
        if self.vars['single_photo_mode'].get():
            f = filedialog.askopenfilename(
                filetypes=[("图片文件", "*.jpg *.jpeg *.png *.tiff *.bmp")]
            )
            if f:
                self.vars['single_image_path'].set(f)
                self.vars['input_folder'].set(os.path.dirname(f))
                base = os.path.splitext(os.path.basename(f))[0]
                ext = os.path.splitext(f)[1] or '.jpg'
                self.vars['output_file'].set(base + '_filmsheet' + ext)
        else:
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
            sf = self.vars['single_image_path'].get()
            if not sf or not os.path.isfile(sf):
                messagebox.showwarning("提示", "请先选择要导出的照片！")
                return
            input_dir = os.path.dirname(sf)

        self.status_lbl.config(text="正在预览...", foreground="gray")
        self.root.update()

        config = {k: v.get() if hasattr(v, 'get') else v for k, v in self.vars.items()}
        config['output_path'] = os.path.join(input_dir, 'preview_temp.jpg')

        proc = FilmProcessor(config)
        img, error = proc.render_preview()

        if error:
            self.status_lbl.config(text="预览失败", foreground="red")
            messagebox.showerror("预览失败", error)
            return

        if img is None:
            self.status_lbl.config(text="预览失败", foreground="red")
            messagebox.showwarning("提示", "没有可处理的图片")
            return

        # 将预览图缩放到窗口可视范围内
        max_w, max_h = 900, 700
        orig_w, orig_h = img.size
        scale = min(max_w / orig_w, max_h / orig_h, 1.0)
        if scale < 1.0:
            disp_w = int(orig_w * scale)
            disp_h = int(orig_h * scale)
            img = img.resize((disp_w, disp_h), Image.Resampling.LANCZOS)

        # 在临时窗口中显示
        preview_win = tk.Toplevel(self.root)
        preview_win.title("FilmSheet v1.6.3 @Escaper — Preview")
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
        ttk.Label(btn_frame, text=f"预览尺寸: {orig_w} × {orig_h} (显示: {img.width} × {img.height})", foreground="gray").pack()
        ttk.Button(btn_frame, text="关闭", command=preview_win.destroy).pack(pady=5)

        # 保持引用防止 GC
        canvas.image_ref = tk_photo

        self.status_lbl.config(text="预览完成", foreground="green")

    def start_process(self):
        single_mode = self.vars['single_photo_mode'].get()
        if not single_mode:
            input_dir = self.vars['input_folder'].get()
            if not input_dir or not os.path.isdir(input_dir):
                messagebox.showerror("Error", "请选择有效的图片来源文件夹！")
                return
        else:
            single_file = self.vars['single_image_path'].get()
            if not single_file or not os.path.isfile(single_file):
                messagebox.showerror("Error", "请选择要导出的单张照片！")
                return
            input_dir = os.path.dirname(single_file)
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
            self.status_lbl.config(text="FilmSheet Done!", foreground="green")
            messagebox.showinfo("Success", f"文件已保存至：\n{self.processor.config['output_path']}")
        elif result == "已取消":
            self.status_lbl.config(text="已取消", foreground="orange")
        else:
            self.status_lbl.config(text="失败", foreground="red")
            messagebox.showerror("Error", result)

    def cancel_process(self):
        if self.processor:
            self.processor.cancel()
            self.status_lbl.config(text="取消中...", foreground="orange")