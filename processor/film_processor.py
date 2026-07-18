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
                bbox = draw.textbbox((0, 0), full_text, font=font_main)
                text_w = bbox[2] - bbox[0]
                slot_widths[col_idx] = max(slot_widths[col_idx], text_w + col_gap)
        total_slot_w = sum(slot_widths)
        available_w = text_area_right - text_area_left
        if total_slot_w > available_w and total_slot_w > 0:
            scale_factor = available_w / total_slot_w
            slot_widths = [int(sw * scale_factor) for sw in slot_widths]

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
                    label_str = lbl
                    draw.text((abs_x, abs_y), label_str, fill=colors["info_label_color"], font=font_main)
                    if val:
                        lbl_bbox = draw.textbbox((0, 0), label_str, font=font_main)
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
        """Build edge text from info_film field or use custom text."""
        custom = self.config.get('edge_text', '')
        if custom:
            return custom
        info_film = self.config.get('info_film', '')
        parts = info_film.split()
        brand = parts[0].upper() if parts else "KODAK"
        # Join remaining parts so "KODAK Portra 400" stays intact
        film_type = ' '.join(parts[1:]) if len(parts) > 1 else "5207"
        return f"{brand}  {film_type} ◀"

    # ------------------------------------------------------------------
    # Image processing
    # ------------------------------------------------------------------

    def cancel(self):
        self.is_cancelled = True

    def crop_to_135_ratio(self, img):
        w, h = img.size
        target_ratio = 36.0 / 24.0
        if w / h > target_ratio:
            new_w = int(h * target_ratio)
            left = (w - new_w) // 2
            return img.crop((left, 0, left + new_w, h))
        else:
            new_h = int(w / target_ratio)
            top = (h - new_h) // 2
            return img.crop((0, top, w, top + new_h))

    def cover_resize_crop(self, img, target_w, target_h):
        img_w, img_h = img.size
        if img_w == 0 or img_h == 0:
            return img
        scale = max(target_w / img_w, target_h / img_h)
        new_w = int(round(img_w * scale))
        new_h = int(round(img_h * scale))
        if scale != 1:
            img = img.resize((new_w, new_h), Image.Resampling.LANCZOS)
        left = (new_w - target_w) // 2
        top = (new_h - target_h) // 2
        img = img.crop((left, top, left + target_w, top + target_h))
        return img

    def process_single_image(self, filepath, thumb_width):
        try:
            img = Image.open(filepath)
            if img.mode != 'RGB':
                img = img.convert('RGB')
            if self.config.get('processing_mode') == 'negative':
                img = ImageOps.invert(img)
            w, h = img.size
            if self.config.get('force_landscape', True) and h > w:
                img = img.rotate(-90, expand=True)
            img = self.crop_to_135_ratio(img)
            target_h = int(thumb_width * 24.0 / 36.0)
            img = img.resize((thumb_width, target_h), Image.Resampling.LANCZOS)
            return img
        except Exception:
            return None

    def _process_120_image(self, filepath, target_ratio, thumb_width):
        try:
            img = Image.open(filepath)
            if img.mode != 'RGB':
                img = img.convert('RGB')
            if self.config.get('processing_mode') == 'negative':
                img = ImageOps.invert(img)
            w, h = img.size
            if self.config['force_landscape'] and h > w:
                img = img.rotate(-90, expand=True)
                w, h = img.size
            current_ratio = w / h
            if current_ratio > target_ratio:
                new_w = int(h * target_ratio)
                left = (w - new_w) // 2
                img = img.crop((left, 0, left + new_w, h))
            else:
                new_h = int(w / target_ratio)
                top = (h - new_h) // 2
                img = img.crop((0, top, w, top + new_h))
            target_h = int(thumb_width / target_ratio)
            img = img.resize((thumb_width, target_h), Image.Resampling.LANCZOS)
            return img
        except Exception:
            return None

    def run(self, status_callback, progress_callback):
        # ---- 自动生成输出文件名（仅当用户未自定义时） ----
        output_file = self.config.get('output_file', 'filmsheet_output.jpg')
        default_names = ['filmsheet_output.jpg', 'filmsheet_output.png', 'filmsheet_output.jpeg']
        if output_file in default_names:
            roll = self.config.get('info_roll', '').strip()
            camera = self.config.get('info_camera', '').strip()
            film = self.config.get('info_film', '').strip()
            shoot_date = self.config.get('info_shoot_date', '').strip()
            parts = [p for p in [roll, camera, film, shoot_date] if p]
            if parts:
                name = '_'.join(parts)
                name = re.sub(r'[\\/*?:"<>|]', '_', name)
                old_path = self.config['output_path']
                dirname = os.path.dirname(old_path)
                if not dirname:
                    dirname = os.getcwd()
                ext = os.path.splitext(old_path)[1]
                if not ext:
                    ext = '.jpg'
                new_path = os.path.join(dirname, name + ext)
                self.config['output_path'] = new_path

        try:
            files = sorted([
                os.path.join(self.config['input_folder'], f)
                for f in os.listdir(self.config['input_folder'])
                if f.lower().endswith(SUPPORTED_FORMATS)
            ])
            if not files:
                return "错误：文件夹中没有图片。"

            is_120 = (self.config['film_format'] == "120")
            batch_enabled = self.config.get('batch_export_enabled', False)

            # ---- 图片预处理（只做一次） ----
            processed_imgs = self._process_images(files, is_120, total_files=len(files),
                                                   status_callback=status_callback,
                                                   progress_callback=progress_callback)
            if processed_imgs is None:
                return "已取消"
            if not processed_imgs:
                return "错误：所有图片处理失败。"

            # ---- 渲染 ----
            if batch_enabled:
                # Render all styles
                styles = list(STYLE_COLORS.keys())
            else:
                styles = [self.config.get('render_style', 'lightbox')]

            results = []
            for style_idx, style in enumerate(styles):
                if self.is_cancelled:
                    return "已取消"

                batch_config = dict(self.config)
                batch_config['render_style'] = style

                # Adjust output path with style suffix
                out_path = batch_config['output_path']
                name, ext = os.path.splitext(out_path)
                batch_config['output_path'] = f"{name}_{style}{ext}"

                # Create a processor with modified config
                batch_proc = FilmProcessor(batch_config)
                # Copy images and state
                batch_proc.images = processed_imgs if hasattr(self, 'images') else []
                batch_proc.is_cancelled = self.is_cancelled

                # Adjust progress range for batch
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

            # Return combined result
            if all(r == "success" for r in results):
                return "success"
            elif "已取消" in results:
                return "已取消"
            else:
                return "; ".join(r for r in results if r != "success")

        except Exception as e:
            return f"错误: {str(e)}"

    def _process_images(self, files, is_120, total_files, status_callback, progress_callback):
        """Process images (crop/resize) and return list of PIL Images."""
        if is_120:
            target_ratio = FILM_FORMAT_RATIOS.get(self.config['sub_format'], 1.0)
            thumb_w = self.config['thumb_width']
            status_callback("正在处理图片...")
            cpu_count = os.cpu_count() or 4
            max_workers = max(4, cpu_count)
            with ThreadPoolExecutor(max_workers=max_workers) as executor:
                future_to_file = {}
                for f in files:
                    future = executor.submit(self._process_120_image, f, target_ratio, thumb_w)
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
            thumb_w = self.config['thumb_width']
            status_callback("正在处理图片...")
            cpu_count = os.cpu_count() or 4
            max_workers = max(4, cpu_count)
            with ThreadPoolExecutor(max_workers=max_workers) as executor:
                future_to_file = {
                    executor.submit(self.process_single_image, f, thumb_w): f
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
            files = sorted([
                os.path.join(self.config['input_folder'], f)
                for f in os.listdir(self.config['input_folder'])
                if f.lower().endswith(SUPPORTED_FORMATS)
            ])
            if not files:
                return None, "文件夹中没有图片"

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
