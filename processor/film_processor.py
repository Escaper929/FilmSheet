# -*- coding: utf-8 -*-

import os
import math
import random
import time
import threading
import subprocess
import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from PIL import Image, ImageDraw, ImageFont, ImageOps

from engine.film_engine import Strict135FilmEngine
from utils.helpers import (
    get_system_font, open_folder, load_config, save_config,
    STYLE_COLORS, LABEL_MAP, INFO_LAYOUT, NO_COLON_FIELDS,
    FILM_FORMAT_RATIOS, SUPPORTED_FORMATS
)
from .renderers_135 import Renderer135
from .renderers_120 import Renderer120
from .config_schema import validate_config, sanitize_config, COMPUTED_FIELDS
from .filename_utils import generate_output_filename
from .edge_text import generate_edge_text as _generate_edge_text_pure
from .image_pipeline import (
    process_135_image as _process_135_image,
    process_120_image as _process_120_image,
    cover_resize_crop as _cover_resize_crop,
)

class FilmProcessor:
    def __init__(self, config):
        self.config = config
        self.is_cancelled = False
        self.engine = Strict135FilmEngine(dpi=300)
        # Font cache: (size, family) -> font object
        self._font_cache: dict[tuple[int, str | None], ImageFont.FreeTypeFont | None] = {}

    def _load_font(self, size: int, family: str | None = None) -> ImageFont.FreeTypeFont | None:
        """Load font with LRU-style caching by (size, family)."""
        key = (size, family)
        if key not in self._font_cache:
            self._font_cache[key] = get_system_font(size)
        return self._font_cache[key]

    # ------------------------------------------------------------------
    # Shared rendering helpers
    # ------------------------------------------------------------------

    def _compute_info_height(self, thumb_w, base_scale, pack_img):
        """Compute total height needed for the info area."""
        has_info = any(
            v for row in INFO_LAYOUT
            for k in row if k and (v := self.config.get(f'info_{k}', ''))
        )
        lang = self.config.get('info_lang', 'en')
        label_idx = 0 if lang == 'zh' else 1
        info_data = {key: self.config.get(f'info_{key}', '') for key in LABEL_MAP}
        active_rows = sum(1 for row in INFO_LAYOUT if any(info_data.get(k, '') for k in row if k))
        info_font_size = int(34 * thumb_w / 400) if thumb_w > 200 else int(34 * base_scale)
        info_line_height = int(52 * thumb_w / 400) if thumb_w > 200 else int(52 * base_scale)
        info_top_padding = int(20 * thumb_w / 400) if thumb_w > 200 else int(20 * base_scale)
        info_bottom_padding = int(15 * thumb_w / 400) if thumb_w > 200 else int(15 * base_scale)

        ih = 0
        if has_info and active_rows > 0:
            ih = info_top_padding + active_rows * info_line_height + info_bottom_padding
        if pack_img and ih == 0:
            ih = int(140 * thumb_w / 400) if thumb_w > 200 else int(140 * base_scale)
        return ih, info_font_size, info_line_height, info_top_padding, info_bottom_padding, info_data, label_idx, has_info

    def _draw_info_block(self, draw, font_main, colors, text_area_left, text_area_right,
                         top_margin, info_top_padding, info_line_height, base_scale, thumb_w):
        """Render the info labels + values on the canvas. Returns (label_idx, slot_widths)."""
        lang = self.config.get('info_lang', 'en')
        label_idx = 0 if lang == 'zh' else 1
        info_data = {key: self.config.get(f'info_{key}', '') for key in LABEL_MAP}

        col_gap = int(40 * thumb_w / 400) if thumb_w > 200 else int(40 * base_scale)
        num_cols = max(len(row) for row in INFO_LAYOUT)
        slot_widths = [0] * num_cols

        # --- Phase 1: Measure all text widths once via textbbox -------------------
        measured_texts = {}  # text -> width (cached to avoid repeated textbbox calls)
        def _text_w(text):
            if text not in measured_texts:
                bbox = draw.textbbox((0, 0), text, font=font_main)
                measured_texts[text] = bbox[2] - bbox[0]
            return measured_texts[text]

        for row_keys in INFO_LAYOUT:
            for col_idx, key in enumerate(row_keys):
                if key is None:
                    continue
                lbl = LABEL_MAP[key][label_idx]
                val = info_data.get(key, '')
                if key in NO_COLON_FIELDS:
                    full_text = f"{lbl} {val}" if val else lbl
                else:
                    full_text = f"{lbl}: {val}" if val else f"{lbl}: "
                text_w = _text_w(full_text)
                slot_widths[col_idx] = max(slot_widths[col_idx], text_w + col_gap)

        total_slot_w = sum(slot_widths)
        available_w = text_area_right - text_area_left
        if total_slot_w > available_w and total_slot_w > 0:
            scale_factor = available_w / total_slot_w
            slot_widths = [int(sw * scale_factor) for sw in slot_widths]

        # --- Phase 2: Render (reuse cached widths where possible) -----------------
        rendered_row = 0
        for r_idx, row_keys in enumerate(INFO_LAYOUT):
            if not any(info_data.get(k, '') for k in row_keys if k):
                continue
            abs_y = top_margin + info_top_padding + rendered_row * info_line_height
            abs_x = text_area_left
            for col_idx, key in enumerate(row_keys):
                if key is None:
                    abs_x += slot_widths[col_idx]
                    continue
                lbl = LABEL_MAP[key][label_idx]
                val = info_data.get(key, '')
                if key in NO_COLON_FIELDS:
                    draw.text((abs_x, abs_y), lbl, fill=colors["info_label_color"], font=font_main)
                    if val:
                        lbl_bbox = draw.textbbox((0, 0), lbl, font=font_main)
                        val_x = abs_x + (lbl_bbox[2] - lbl_bbox[0]) + int(8 * thumb_w / 400) if thumb_w > 200 else int(8 * base_scale)
                        draw.text((val_x, abs_y), val, fill=colors["info_text_color"], font=font_main)
                else:
                    label_str = f"{lbl}: "
                    draw.text((abs_x, abs_y), label_str, fill=colors["info_label_color"], font=font_main)
                    if val:
                        lbl_bbox = draw.textbbox((0, 0), label_str, font=font_main)
                        val_x = abs_x + (lbl_bbox[2] - lbl_bbox[0])
                        draw.text((val_x, abs_y), val, fill=colors["info_text_color"], font=font_main)
                abs_x += slot_widths[col_idx]
            rendered_row += 1
        return label_idx, slot_widths

    def _generate_edge_text(self):
        """Build edge text from info_film field or use custom text.

        Delegates to the pure function for brand/type mapping.
        """
        custom = self.config.get('edge_text', '').strip()
        info_film = self.config.get('info_film', '').strip()
        return _generate_edge_text_pure(info_film, custom)

    # _draw_edge_text_on_strip removed: dead code (never called, referenced undefined `font_size`).
    # Edge text is drawn directly in renderers_135.py / renderers_120.py draw_strip_decoration().

    def _draw_triangle(self, draw, cx, cy, size, color):
        """Draw a left-pointing isosceles triangle with 30° apex angle.

        Apex at left (cx, cy), pointing left. Base angles are 75°.
        """
        import math
        # 30° apex → half-angle = 15°
        half_angle_rad = math.radians(15)
        # Triangle width (from tip to base center)
        tri_w = size * math.cos(half_angle_rad)
        # Half height of base
        tri_h = size * math.sin(half_angle_rad)

        # Points: tip (left), top-right, bottom-right
        pts = [
            (cx - tri_w, cy),           # tip (left)
            (cx + tri_w * 0.1, cy - tri_h),   # top-right
            (cx + tri_w * 0.1, cy + tri_h),   # bottom-right
        ]
        draw.polygon(pts, fill=color)

    # ------------------------------------------------------------------
    # Image processing
    # ------------------------------------------------------------------

    def cancel(self):
        self.is_cancelled = True

    def crop_to_135_ratio(self, img):
        """Delegate to image_pipeline for future portability."""
        from .image_pipeline import _crop_to_135_ratio as _crop
        return _crop(img)

    def cover_resize_crop(self, img, target_w, target_h):
        """Delegate to image_pipeline for future portability."""
        return _cover_resize_crop(img, target_w, target_h)

    def process_single_image(self, filepath, thumb_width):
        """Delegate to image_pipeline for future portability."""
        return _process_135_image(
            filepath, thumb_width,
            self.config.get('processing_mode', 'positive'),
            self.config.get('force_landscape', True)
        )

    def _process_120_image(self, filepath, target_ratio, thumb_width):
        """Delegate to image_pipeline for future portability."""
        return _process_120_image(
            filepath, target_ratio, thumb_width,
            self.config.get('processing_mode', 'positive'),
            self.config.get('force_landscape', True)
        )

    def _resolve_image_list(self):
        """Return (file_list, error_msg) from either single_image_path or input_folder."""
        # Only use single_image_path if single_photo_mode is explicitly enabled
        if self.config.get('single_photo_mode', False):
            single = self.config.get('single_image_path', '')
            if single and os.path.isfile(single):
                return [single], None
        folder = self.config.get('input_folder', '')
        if not folder or not os.path.isdir(folder):
            return [], "请输入有效的图片来源！"
        files = sorted([
            os.path.join(folder, f)
            for f in os.listdir(folder)
            if f.lower().endswith(SUPPORTED_FORMATS)
        ])
        if not files:
            return [], "文件夹中没有图片。"
        return files, None

    def run(self, status_callback, progress_callback):
        # Validate and sanitize config
        is_valid, errors = validate_config(self.config)
        if not is_valid:
            return f"配置错误: {'; '.join(errors)}"

        sanitized = sanitize_config(self.config)
        config = dict(sanitized)
        config.update(self.config)  # Preserve user-set values

        # Generate output filename
        output_file = config.get('output_file', 'filmsheet_output.jpg')
        default_names = {'filmsheet_output.jpg', 'filmsheet_output.png', 'filmsheet_output.jpeg'}
        if output_file.strip() in default_names:
            new_path = generate_output_filename(
                output_file,
                config.get('info_roll', ''),
                config.get('info_camera', ''),
                config.get('info_film', ''),
                config.get('info_shoot_date', ''),
                os.path.dirname(config.get('output_path', '.')),
            )
            # Only update output_path if the new path actually has a directory
            if '/' in new_path or os.sep in new_path or os.path.dirname(new_path):
                config['output_path'] = new_path

        files, err = self._resolve_image_list()
        if err:
            return f"错误：{err}"

        try:
            is_120 = (config['film_format'] == "120")
            batch_enabled = config.get('batch_export_enabled', False)

            # Single-photo mode: compress layout to one column so canvas
            # is only as wide as the frame itself
            if len(files) == 1 and config.get('single_photo_mode', False):
                config['columns'] = 1

            # ---- 图片预处理（只做一次） ----
            processed_imgs = self._process_images(files, is_120, total_files=len(files),
                                                  config=config,
                                                  status_callback=status_callback,
                                                  progress_callback=progress_callback)
            if processed_imgs is None:
                return "已取消"
            if not processed_imgs:
                return "错误：所有图片处理失败。"

            # ---- 渲染 ----
            if batch_enabled:
                styles = list(STYLE_COLORS.keys())
            else:
                styles = [config.get('render_style', 'lightbox')]

            results = []
            for style_idx, style in enumerate(styles):
                if self.is_cancelled:
                    return "已取消"

                batch_config = dict(config)
                batch_config['render_style'] = style

                out_path = batch_config['output_path']
                name, ext = os.path.splitext(out_path)
                batch_config['output_path'] = f"{name}_{style}{ext}"

                batch_proc = FilmProcessor(batch_config)
                batch_proc.images = processed_imgs if hasattr(self, 'images') else []
                batch_proc.is_cancelled = self.is_cancelled

                if batch_enabled and len(styles) > 1:
                    start_pct = 50 + style_idx * (50 // len(styles))
                    end_pct = 50 + (style_idx + 1) * (50 // len(styles))

                    def batch_sc(msg):
                        status_callback(msg)
                    def batch_pc(val, msg):
                        adjusted = start_pct + int((val - 50) * (end_pct - start_pct) / 50) if end_pct > start_pct else val
                        progress_callback(min(adjusted, 100), f"渲染[{style}]: {msg}")

                    result = batch_proc._render_135(processed_imgs, batch_sc, batch_pc) if not is_120 \
                             else batch_proc._render_120(processed_imgs, batch_sc, batch_pc)
                else:
                    result = batch_proc._render_135(processed_imgs, status_callback, progress_callback) if not is_120 \
                             else batch_proc._render_120(processed_imgs, status_callback, progress_callback)

                results.append(result)

            if all(r == "success" for r in results):
                return "success"
            elif "已取消" in results:
                return "已取消"
            else:
                return "; ".join(r for r in results if r != "success")

        except Exception as e:
            return f"错误: {str(e)}"

    def _process_images(self, files, is_120, total_files, config, status_callback, progress_callback):
        """Process images (crop/resize) and return list of PIL Images."""
        cfg = config
        if is_120:
            target_ratio = FILM_FORMAT_RATIOS.get(cfg.get('sub_format', '66'), 1.0)
            thumb_w = cfg['thumb_width']
            processing_mode = cfg.get('processing_mode', 'positive')
            force_landscape = cfg.get('force_landscape', True)
            status_callback("正在处理图片...")
            cpu_count = os.cpu_count() or 4
            max_workers = max(4, cpu_count)
            with ThreadPoolExecutor(max_workers=max_workers) as executor:
                future_to_file = {}
                for f in files:
                    future = executor.submit(
                        _process_120_image, f, target_ratio, thumb_w,
                        processing_mode, force_landscape
                    )
                    future_to_file[future] = f
                imgs = []
                for i, future in enumerate(as_completed(future_to_file)):
                    if self.is_cancelled:
                        return None
                    img = future.result()
                    if img:
                        imgs.append(img)
                    progress_callback(int((i + 1) / total_files * 50), f"处理图片: {i+1}/{total_files}")
            return imgs
        else:
            thumb_w = cfg['thumb_width']
            processing_mode = cfg.get('processing_mode', 'positive')
            force_landscape = cfg.get('force_landscape', True)
            status_callback("正在处理图片...")
            cpu_count = os.cpu_count() or 4
            max_workers = max(4, cpu_count)
            with ThreadPoolExecutor(max_workers=max_workers) as executor:
                future_to_file = {
                    executor.submit(_process_135_image, f, thumb_w, processing_mode, force_landscape): f
                    for f in files
                }
                imgs = []
                for i, future in enumerate(as_completed(future_to_file)):
                    if self.is_cancelled:
                        return None
                    img = future.result()
                    if img:
                        imgs.append(img)
                    progress_callback(int((i + 1) / total_files * 50), f"处理图片: {i+1}/{total_files}")
            return imgs

    def _render_135(self, images, status_callback, progress_callback):
        """使用物理级 135 引擎渲染（支持多种子画幅）"""
        renderer = Renderer135(
            self.config, self, images,
            status_callback=status_callback,
            progress_callback=progress_callback,
            is_preview=False)
        return renderer.render()

    def render_preview(self):
        """快速预览渲染，不保存文件，返回 PIL Image 对象。"""
        try:
            files, err = self._resolve_image_list()
            if err:
                return None, err

            # Single-photo mode: compress layout to one column so canvas
            # is only as wide as the frame itself (same as run()).
            if len(files) == 1 and self.config.get('single_photo_mode', False):
                self.config['columns'] = 1

            is_120 = (self.config['film_format'] == "120")

            if is_120:
                target_ratio = FILM_FORMAT_RATIOS.get(self.config['sub_format'], 1.0)
                processed_imgs = []
                with ThreadPoolExecutor(max_workers=os.cpu_count() or 4) as executor:
                    futures = {
                        executor.submit(self._process_120_image, f, target_ratio, 80): f
                        for f in files
                    }
                    for future in as_completed(futures):
                        if self.is_cancelled:
                            return None, "已取消"
                        img = future.result()
                        if img:
                            processed_imgs.append(img)
                if not processed_imgs:
                    return None, "所有图片处理失败"
                return self._render_preview_120(processed_imgs), None
            else:
                processed_imgs = []
                with ThreadPoolExecutor(max_workers=os.cpu_count() or 4) as executor:
                    futures = {
                        executor.submit(self.process_single_image, f, 80): f
                        for f in files
                    }
                    for future in as_completed(futures):
                        if self.is_cancelled:
                            return None, "已取消"
                        img = future.result()
                        if img:
                            processed_imgs.append(img)
                if not processed_imgs:
                    return None, "所有图片处理失败"
                return self._render_preview_135(processed_imgs), None
        except Exception as e:
            return None, str(e)

    def _render_preview_135(self, images):
        """轻量级 135 预览渲染，无 AA，无保存，无文件夹打开。"""
        renderer = Renderer135(
            self.config, self, images,
            status_callback=lambda _: None,
            progress_callback=lambda *_: None,
            is_preview=True)
        layout = renderer.compute_layout()
        canvas, draw, layout = renderer._build_canvas(layout)
        renderer._draw_pack_image(canvas, layout)
        renderer._draw_info_block(canvas, layout)
        renderer._draw_strips(canvas, layout)
        renderer._draw_watermark(canvas, layout)
        return renderer._downscale_if_aa(canvas, layout)

    def _render_preview_120(self, images):
        """轻量级 120 预览渲染，无 AA，无保存，无文件夹打开。"""
        renderer = Renderer120(
            self.config, self, images,
            status_callback=lambda _: None,
            progress_callback=lambda *_: None,
            is_preview=True)
        layout = renderer.compute_layout()
        canvas, draw, layout = renderer._build_canvas(layout)
        renderer._draw_pack_image(canvas, layout)
        renderer._draw_info_block(canvas, layout)
        renderer._draw_strips(canvas, layout)
        renderer._draw_watermark(canvas, layout)
        return renderer._downscale_if_aa(canvas, layout)

    def _render_120(self, images, status_callback, progress_callback):
        renderer = Renderer120(
            self.config, self, images,
            status_callback=status_callback,
            progress_callback=progress_callback,
            is_preview=False)
        return renderer.render()
