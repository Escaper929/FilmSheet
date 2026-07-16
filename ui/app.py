# -*- coding: utf-8 -*-

import os
import sys
import threading
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

from processor.film_processor import FilmProcessor
from utils.helpers import load_config, save_config, add_pack_image_history, LABEL_MAP, INFO_LAYOUT, NO_COLON_FIELDS

class App:
    def __init__(self, root):
        self.root = root
        self.root.title("FilmSheet")
        self.root.geometry("620x750")

        cfg = load_config()
        self.pack_history = cfg.get("pack_images", [])

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
            'sub_format': tk.StringVar(value="66"),
            'info_lang': tk.StringVar(value="en"),
            'pack_image': tk.StringVar(),
            'pack_position': tk.StringVar(value=cfg.get("pack_position", "left")),
            'pack_border_stroke': tk.BooleanVar(value=cfg.get("pack_border_stroke", True)),
            'processing_mode': tk.StringVar(value="positive"),
            'perf_mode': tk.StringVar(value="Auto"),
            'render_style': tk.StringVar(value=cfg.get("render_style", "lightbox")),
        }

        for key in LABEL_MAP:
            self.vars[f'info_{key}'] = tk.StringVar()

        self.processor = None
        self.info_labels = {}
        self.build_ui()

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

    def save_pack_config(self, event=None):
        cfg = load_config()
        cfg["pack_position"] = self.vars['pack_position'].get()
        cfg["pack_border_stroke"] = self.vars['pack_border_stroke'].get()
        cfg["render_style"] = self.vars['render_style'].get()
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

        # ---- 基本设置 ----
        basic_frame = ttk.LabelFrame(main_frame, text="基本设置", padding="10")
        basic_frame.grid(row=0, column=0, columnspan=4, sticky=tk.EW, pady=5)
        ttk.Label(basic_frame, text="图片来源:").grid(row=0, column=0, sticky=tk.W, pady=5)
        ttk.Entry(basic_frame, textvariable=self.vars['input_folder'], width=35).grid(row=0, column=1, padx=5)
        ttk.Button(basic_frame, text="浏览...", command=self.browse_input).grid(row=0, column=2, padx=5)
        ttk.Label(basic_frame, text="输出:").grid(row=1, column=0, sticky=tk.W, pady=5)
        ttk.Entry(basic_frame, textvariable=self.vars['output_file'], width=35).grid(row=1, column=1, columnspan=2, sticky=tk.EW)

        # ---- 参数设置 ----
        param_frame = ttk.LabelFrame(main_frame, text="参数设置", padding="10")
        param_frame.grid(row=1, column=0, columnspan=4, sticky=tk.EW, pady=5)

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
        self.sub_combo = ttk.Combobox(param_frame, textvariable=self.vars['sub_format'],
                                      values=["645", "66", "67", "68", "69", "617"], state="disabled", width=6)
        self.sub_combo.grid(row=0, column=4, sticky=tk.W, padx=5)

        ttk.Label(param_frame, text="缩略图宽:").grid(row=1, column=0, sticky=tk.W, pady=5)
        ttk.Spinbox(param_frame, from_=300, to=800, textvariable=self.vars['thumb_width'], width=6).grid(row=1, column=1, sticky=tk.W)
        ttk.Label(param_frame, text="每行列数:").grid(row=1, column=2, sticky=tk.W, padx=(20,0))
        ttk.Spinbox(param_frame, from_=3, to=10, textvariable=self.vars['columns'], width=6).grid(row=1, column=3, sticky=tk.W)
        ttk.Checkbutton(param_frame, text="强制横向", variable=self.vars['force_landscape']).grid(row=1, column=4, sticky=tk.W, padx=(10,0))

        ttk.Label(param_frame, text="齿孔模式:").grid(row=2, column=0, sticky=tk.W, pady=5)
        ttk.Combobox(param_frame, textvariable=self.vars['perf_mode'],
                     values=["Auto", "KS (民用)", "BH (电影)"], state="readonly", width=10).grid(row=2, column=1, sticky=tk.W)

        ttk.Label(param_frame, text="渲染风格:").grid(row=2, column=2, sticky=tk.W, padx=(20,0))
        style_radio = ttk.Frame(param_frame)
        style_radio.grid(row=2, column=3, columnspan=2, sticky=tk.W)
        ttk.Radiobutton(style_radio, text="灯板正片", variable=self.vars['render_style'],
                        value="lightbox", command=self.save_pack_config).pack(side=tk.LEFT)
        ttk.Radiobutton(style_radio, text="接触印相", variable=self.vars['render_style'],
                        value="contact_sheet", command=self.save_pack_config).pack(side=tk.LEFT, padx=(10,0))

        # ---- 边字设置 ----
        edge_frame = ttk.LabelFrame(main_frame, text="边字设置", padding="5")
        edge_frame.grid(row=2, column=0, columnspan=4, sticky=tk.EW, pady=5)

        ttk.Label(edge_frame, text="自定义内容:").grid(row=0, column=0, sticky=tk.W)
        ttk.Entry(edge_frame, textvariable=self.vars['edge_text'], width=25).grid(row=0, column=1, sticky=tk.W, padx=(0,10))
        ttk.Label(edge_frame, text="(留空则从'Film'字段自动生成)", foreground="gray").grid(row=0, column=2, sticky=tk.W)

        # ---- 胶卷包装图 ----
        pack_frame = ttk.LabelFrame(main_frame, text="胶卷包装图", padding="10")
        pack_frame.grid(row=3, column=0, columnspan=4, sticky=tk.EW, pady=5)

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

        self.refresh_pack_combo()

        # ---- 拍摄信息 ----
        info_frame = ttk.LabelFrame(main_frame, text="拍摄信息记录 (选填)", padding="10")
        info_frame.grid(row=4, column=0, columnspan=4, sticky=tk.EW, pady=5)

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
        out_frame.grid(row=5, column=0, columnspan=4, sticky=tk.EW, pady=5)

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

        # ---- 控制按钮 ----
        ctrl_frame = ttk.Frame(main_frame)
        ctrl_frame.grid(row=6, column=0, columnspan=4, pady=15)

        self.progress_bar = ttk.Progressbar(ctrl_frame, orient=tk.HORIZONTAL, length=400, mode='determinate')
        self.progress_bar.pack(side=tk.TOP, fill=tk.X, pady=(0,10))

        btn_frame = ttk.Frame(ctrl_frame)
        btn_frame.pack(side=tk.BOTTOM)
        self.start_btn = ttk.Button(btn_frame, text="开始生成", command=self.start_process)
        self.start_btn.pack(side=tk.LEFT, padx=5)
        self.cancel_btn = ttk.Button(btn_frame, text="取消", command=self.cancel_process, state=tk.DISABLED)
        self.cancel_btn.pack(side=tk.LEFT, padx=5)
        self.status_lbl = ttk.Label(main_frame, text="FilmSheet Ready", foreground="gray")
        self.status_lbl.grid(row=7, column=0, columnspan=4, pady=5)

    def toggle_sub_format(self):
        self.sub_combo.config(state="readonly" if self.vars['film_format'].get() == "120" else "disabled")

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
        folder = filedialog.askdirectory()
        if folder:
            self.vars['input_folder'].set(folder)

    def start_process(self):
        input_dir = self.vars['input_folder'].get()
        if not input_dir or not os.path.isdir(input_dir):
            messagebox.showerror("Error", "请选择有效的图片来源文件夹！")
            return

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