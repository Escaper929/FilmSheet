# -*- coding: utf-8 -*-

import os
import math
import random
import time
import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from PIL import Image, ImageDraw, ImageFont, ImageOps

from engine.film_engine import Strict135FilmEngine
from utils.helpers import (
    get_system_font, open_folder, load_config, save_config,
    STYLE_COLORS, LABEL_MAP, INFO_LAYOUT, NO_COLON_FIELDS,
    FILM_FORMAT_RATIOS, SUPPORTED_FORMATS
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

    def draw_edge_text_at(self, draw, text, x, y, font_size, style="lightbox"):
        if not text:
            return
        font = self._load_font(font_size)
        if not font:
            return
        colors = STYLE_COLORS.get(style, STYLE_COLORS["lightbox"])
        color = colors["text_color"]
        draw.text((x, int(y)), text, fill=color, font=font, anchor="mm")

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

    def _apply_cyanotype(self, img):
        """Convert image to cyanotype effect.

        Real cyanotype: light areas become deep Prussian blue,
        shadow areas become paper-white/light blue.
        Uses luminance mapping to simulate UV exposure.
        """
        # Build cyanotype color LUT for each luminance value
        cyan_lut = []
        for lum in range(256):
            if lum > 200:
                r, g, b = 25, 55, 145
            elif lum > 150:
                t = (lum - 150) / 50.0
                r = int(25 + t * 45)
                g = int(55 + t * 55)
                b = int(145 + t * 20)
            elif lum > 80:
                t = (lum - 80) / 70.0
                r = int(70 + t * 55)
                g = int(100 + t * 40)
                b = int(170 + t * 20)
            elif lum > 30:
                t = (lum - 30) / 50.0
                r = int(150 + t * 50)
                g = int(175 + t * 45)
                b = int(210 + t * 25)
            else:
                r = int(220 + (lum / 30) * 15)
                g = int(228 + (lum / 30) * 10)
                b = int(240 + (lum / 30) * 10)
            cyan_lut.append((r, g, b))

        # Convert to grayscale, then remap each pixel via LUT
        gray = img.convert('L')
        w, h = gray.size
        result = Image.new('RGB', (w, h))
        px = gray.load()
        rx = result.load()
        for y in range(h):
            for x in range(w):
                rx[x, y] = cyan_lut[px[x, y]]
        return result

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
            # Cyanotype: invert positive scan to simulate negative exposure
            if self.config.get('render_style') == 'cyanotype':
                img = self._apply_cyanotype(img)
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
            # Cyanotype: invert positive scan to simulate negative exposure
            if self.config.get('render_style') == 'cyanotype':
                img = self._apply_cyanotype(img)
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
        # ---- 继续原有流程 ----
        try:
            files = sorted([
                os.path.join(self.config['input_folder'], f)
                for f in os.listdir(self.config['input_folder'])
                if f.lower().endswith(SUPPORTED_FORMATS)
            ])
            if not files:
                return "错误：文件夹中没有图片。"

            total_files = len(files)
            is_120 = (self.config['film_format'] == "120")

            if is_120:
                target_ratio = FILM_FORMAT_RATIOS.get(self.config['sub_format'], 1.0)
                thumb_w = self.config['thumb_width']
                processed_imgs = []
                status_callback("正在处理 120 图片...")
                cpu_count = os.cpu_count() or 4
                max_workers = max(4, cpu_count)
                with ThreadPoolExecutor(max_workers=max_workers) as executor:
                    future_to_file = {}
                    for f in files:
                        future = executor.submit(
                            self._process_120_image, f, target_ratio, thumb_w
                        )
                        future_to_file[future] = f
                    for i, future in enumerate(as_completed(future_to_file)):
                        if self.is_cancelled:
                            return "已取消"
                        img = future.result()
                        if img:
                            processed_imgs.append(img)
                        progress_callback(int((i + 1) / total_files * 50), f"处理图片: {i+1}/{total_files}")
                if not processed_imgs:
                    return "错误：所有图片处理失败。"
                return self._render_120(processed_imgs, status_callback, progress_callback)
            else:
                thumb_w = self.config['thumb_width']
                processed_imgs = []
                status_callback("正在处理 135 图片...")
                cpu_count = os.cpu_count() or 4
                max_workers = max(4, cpu_count)
                with ThreadPoolExecutor(max_workers=max_workers) as executor:
                    future_to_file = {
                        executor.submit(self.process_single_image, f, thumb_w): f
                        for f in files
                    }
                    for i, future in enumerate(as_completed(future_to_file)):
                        if self.is_cancelled:
                            return "已取消"
                        img = future.result()
                        if img:
                            processed_imgs.append(img)
                        progress_callback(int((i + 1) / total_files * 50), f"处理图片: {i+1}/{total_files}")
                if not processed_imgs:
                    return "错误：所有图片处理失败。"
                return self._render_135(processed_imgs, status_callback, progress_callback)
        except Exception as e:
            return f"错误: {str(e)}"

    def _render_135(self, images, status_callback, progress_callback):
        """使用物理级 135 引擎渲染（支持多种子画幅）"""
        cols = self.config['columns']
        rows = math.ceil(len(images) / cols)
        thumb_w = self.config['thumb_width']
        spacing = int(self.config['spacing'] * thumb_w / 400)

        # 抗锯齿缩放系数
        aa_scale = 4
        self._aa_scale = aa_scale

        # ---- 子画幅配置 ----
        sub_format = self.config.get('sub_format', '标准 36×24')

        # 画幅配置表 (宽, 高, 占用齿孔数)
        sub_format_configs = {
            "标准 36×24": (36, 24, 8),
            "半格 18×24": (18, 24, 4),
            "方形 24×24": (24, 24, 5),
            "XPan 65×24": (65, 24, 14),
        }
        frame_w_mm, frame_h_mm, perfs_per_frame = sub_format_configs.get(
            sub_format, (36, 24, 8)
        )

        # 齿孔类型
        perf_type = self.engine.determine_perf_type(
            self.config.get('info_film', ''),
            self.config.get('perf_mode', 'Auto')
        )
        pitch_mm = self.engine.get_perf_pitch(perf_type)
        advance_mm = pitch_mm * perfs_per_frame
        scale_factor = thumb_w / frame_w_mm

        # ---- 物理尺寸 ----
        strip_h = int(35.0 * scale_factor)
        perf_center_offset_px = int((2.01 + 2.794/2.0) * scale_factor)
        frame_top_offset_px = int((35.0 - 24.0) / 2.0 * scale_factor)
        frame_h_px = int(frame_h_mm * scale_factor)
        frame_w_px = int(frame_w_mm * scale_factor)

        # 齿孔尺寸
        perf_h_px = int(2.794 * scale_factor)
        perf_w_ks_px = int(1.981 * scale_factor)
        perf_w_bh_px = int(1.854 * scale_factor)
        perf_r_px = int(0.508 * scale_factor)
        bh_cd_px = int(0.35 * scale_factor)
        pitch_px = int(pitch_mm * scale_factor)
        advance_px = int(advance_mm * scale_factor)

        # ---- 布局计算 ----
        content_w = (cols * frame_w_px) + ((cols + 1) * spacing)
        side_margin = int(50 * thumb_w / 400)
        top_margin = int(25 * thumb_w / 400)
        total_w = content_w + (side_margin * 2) + int(100 * thumb_w / 400)
        bag_gap = int(50 * thumb_w / 400)

        # ---- 包装图 ----
        pack_img_path = self.config.get('pack_image', '')
        pack_position = self.config.get('pack_position', 'left')
        pack_img = None
        if pack_img_path and os.path.exists(pack_img_path):
            try:
                pack_img = Image.open(pack_img_path).convert('RGB')
            except Exception:
                pack_img = None

        has_pack_stroke = self.config.get('pack_border_stroke', True)
        pack_border = max(2, int(2 * thumb_w / 400)) if has_pack_stroke else 0
        pack_gap = int(20 * thumb_w / 400)

        # 共享 helper：计算 info 区域高度
        info_height, info_font_size, info_line_height, info_top_padding, info_bottom_padding, info_data, label_idx, has_info = \
            self._compute_info_height(thumb_w, thumb_w / 400, pack_img)

        info_to_film_gap = int(65 * thumb_w / 400)
        top_area_height = top_margin + info_height + info_to_film_gap
        top_region_height = top_margin + info_height
        if info_height == 0:
            bottom_margin = int(top_region_height * 2.0)
        else:
            bottom_margin = int(top_region_height * 1.6)
        total_h = int(top_area_height + (rows * strip_h) + ((rows - 1) * bag_gap) + bottom_margin)

        render_style = self.config.get('render_style', 'lightbox')
        colors = STYLE_COLORS.get(render_style, STYLE_COLORS["lightbox"])

        status_callback("正在绘制 135 底片...")

        # ---- 创建 4x 放大画布 ----
        big_total_w = total_w * aa_scale
        big_total_h = total_h * aa_scale
        big_canvas = Image.new('RGB', (big_total_w, big_total_h), colors["canvas_bg"])
        big_draw = ImageDraw.Draw(big_canvas)

        # ---- 所有坐标乘以 aa_scale ----
        big_current_y = top_area_height * aa_scale
        big_side_margin = side_margin * aa_scale
        big_spacing = spacing * aa_scale
        big_frame_w_px = frame_w_px * aa_scale
        big_frame_h_px = frame_h_px * aa_scale
        big_strip_h = strip_h * aa_scale
        big_bag_gap = bag_gap * aa_scale
        big_perf_center_offset_px = perf_center_offset_px * aa_scale
        big_frame_top_offset_px = frame_top_offset_px * aa_scale
        big_pitch_px = pitch_px * aa_scale

        # 大画布上的齿孔参数
        big_perf_h_px = perf_h_px * aa_scale
        big_perf_w_ks_px = perf_w_ks_px * aa_scale
        big_perf_w_bh_px = perf_w_bh_px * aa_scale
        big_perf_r_px = perf_r_px * aa_scale
        big_bh_cd_px = bh_cd_px * aa_scale

        # ---- 边字字体大小缩放到85% ----
        base_font = int(14 * thumb_w / 400) if thumb_w > 200 else 14
        big_edge_font_sz = int(base_font * 0.85) * aa_scale

        # ---- 包装图 ----
        big_text_area_left = big_side_margin
        big_text_area_right = big_total_w - big_side_margin
        if pack_img and info_height > 0:
            orig_w, orig_h = pack_img.size
            pack_size_pct = self.config.get('pack_size', 80)
            if isinstance(pack_size_pct, str):
                try:
                    pack_size_pct = int(pack_size_pct)
                except ValueError:
                    pack_size_pct = 80
            top_blank_height = top_margin + info_height + info_to_film_gap
            pack_h_display = int(top_blank_height * pack_size_pct / 100.0)
            if pack_h_display < 20:
                pack_h_display = 20
            pack_w_display = int(pack_h_display * (orig_w / orig_h))
            max_allow_w = int(total_w * 0.35)
            if pack_w_display > max_allow_w:
                pack_w_display = max_allow_w
                pack_h_display = int(pack_w_display * (orig_h / orig_w))
            if pack_w_display > 0 and pack_h_display > 0:
                big_pack = pack_img.resize(
                    (pack_w_display * aa_scale, pack_h_display * aa_scale),
                    Image.Resampling.LANCZOS
                )
                pack_y = (top_blank_height - pack_h_display) // 2 * aa_scale
                if pack_position == 'left':
                    pack_x = big_side_margin
                    if has_pack_stroke:
                        pb = pack_border * aa_scale
                        big_draw.rectangle([
                            pack_x - pb, pack_y - pb,
                            pack_x + pack_w_display * aa_scale + pb,
                            pack_y + pack_h_display * aa_scale + pb
                        ], outline=colors["pack_border"], width=pb)
                    big_canvas.paste(big_pack, (pack_x, pack_y))
                    big_text_area_left = pack_x + pack_w_display * aa_scale + pack_gap * aa_scale
                else:
                    pack_x = big_total_w - big_side_margin - pack_w_display * aa_scale
                    if has_pack_stroke:
                        pb = pack_border * aa_scale
                        big_draw.rectangle([
                            pack_x - pb, pack_y - pb,
                            pack_x + pack_w_display * aa_scale + pb,
                            pack_y + pack_h_display * aa_scale + pb
                        ], outline=colors["pack_border"], width=pb)
                    big_canvas.paste(big_pack, (pack_x, pack_y))
                    big_text_area_right = pack_x - pack_gap * aa_scale

        # ---- 大画布上的齿孔绘制函数 ----
        def draw_perf_big(draw, cx, cy, perf_fill, perf_type):
            h = big_perf_h_px
            if perf_type == "KS":
                w = big_perf_w_ks_px
                r = big_perf_r_px
                draw.rounded_rectangle(
                    [cx - w//2, cy - h//2, cx + w//2, cy + h//2],
                    radius=r, fill=perf_fill, outline=None
                )
            else:
                w = big_perf_w_bh_px
                cd = big_bh_cd_px
                draw.rectangle(
                    [cx - w//2, cy - h//2 + cd, cx + w//2, cy + h//2 - cd],
                    fill=perf_fill, outline=None
                )
                draw.ellipse(
                    [cx - w//2, cy - h//2, cx + w//2, cy - h//2 + 2*cd],
                    fill=perf_fill, outline=None
                )
                draw.ellipse(
                    [cx - w//2, cy + h//2 - 2*cd, cx + w//2, cy + h//2],
                    fill=perf_fill, outline=None
                )

        # ---- 在放大画布上绘制拍摄信息 ----
        if has_info:
            font_main = self._load_font(info_font_size * aa_scale)
            if font_main:
                self._draw_info_block(
                    big_draw, font_main, colors,
                    big_text_area_left, big_text_area_right,
                    top_margin * aa_scale, info_top_padding * aa_scale,
                    info_line_height * aa_scale, thumb_w / 400.0, thumb_w
                )

        # ---- 边字 ----
        edge_text = self._generate_edge_text()
        film_base = colors["film_base"]
        perf_fill = colors["perf_fill"]

        big_img_idx = 0
        for row in range(rows):
            if self.is_cancelled:
                return "已取消"

            y1 = int(big_current_y)
            y2 = int(big_current_y + big_strip_h)
            big_draw.rectangle([0, y1, big_total_w, y2], fill=film_base)

            big_perf_y_top = big_current_y + big_perf_center_offset_px
            big_perf_y_bottom = big_current_y + big_strip_h - big_perf_center_offset_px
            big_y_img_top = big_current_y + big_frame_top_offset_px

            # ---- 齿孔 ----
            x = 25 * aa_scale
            while x < big_total_w - 25 * aa_scale:
                draw_perf_big(big_draw, int(x), int(big_perf_y_top), perf_fill, perf_type)
                draw_perf_big(big_draw, int(x), int(big_perf_y_bottom), perf_fill, perf_type)
                x += big_pitch_px

            # ---- 边字 ----
            edge_text = self._generate_edge_text()

            # Use a per-render seed so positions vary between runs but stay stable within one render
            render_seed = int(time.time()) ^ (row * 7919 + big_img_idx)
            rng = random.Random(render_seed)
            num_occurrences = rng.choice([2, 3, 4])

            margin = int(40 * thumb_w / 400) * aa_scale
            if num_occurrences == 2:
                base_ratios = [0.25, 0.75]
            elif num_occurrences == 3:
                base_ratios = [0.18, 0.50, 0.82]
            else:
                base_ratios = [0.12, 0.38, 0.62, 0.88]

            offset_range = int(0.08 * big_total_w)
            selected_x = []
            for ratio in base_ratios:
                base_x = margin + int(ratio * (big_total_w - 2*margin))
                offset = rng.randint(-offset_range, offset_range)
                x = max(margin, min(big_total_w - margin, base_x + offset))
                selected_x.append(x)
            selected_x.sort()

            edge_y_offset = int(10 * thumb_w / 400) * aa_scale
            edge_y_top = big_current_y + edge_y_offset
            edge_y_bottom = big_current_y + big_strip_h - edge_y_offset

            font = self._load_font(big_edge_font_sz)
            if font:
                color = colors["text_color"]
                for x_pos in selected_x:
                    big_draw.text((x_pos, edge_y_top), edge_text, fill=color, font=font, anchor="mm")
                    big_draw.text((x_pos, edge_y_bottom), edge_text, fill=color, font=font, anchor="mm")

            # ---- 放置图片 ----
            start_col = 2 if row == 0 else 0
            for col in range(start_col, cols):
                if big_img_idx >= len(images):
                    break
                x_pos = big_side_margin + big_spacing + col * (big_frame_w_px + big_spacing)
                img_original = images[big_img_idx]
                big_img = self.cover_resize_crop(img_original, big_frame_w_px, big_frame_h_px)
                big_canvas.paste(big_img, (int(x_pos), int(big_y_img_top)))
                big_img_idx += 1

            big_current_y += big_strip_h + big_bag_gap
            progress_callback(50 + int((row + 1) / rows * 50), f"渲染行: {row+1}/{rows}")

        # ---- 缩回原尺寸 ----
        status_callback("正在应用抗锯齿...")
        canvas = big_canvas.resize((total_w, total_h), Image.Resampling.LANCZOS)

        # ---- 保存 ----
        status_callback("正在保存文件...")
        out_path = self.config['output_path']
        if out_path.lower().endswith('.png'):
            canvas.save(out_path, compress_level=1)
        else:
            canvas.save(out_path, quality=self.config['quality'], optimize=True)

        open_folder(out_path)
        return "success"

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
        cols = self.config['columns']
        rows = math.ceil(len(images) / cols)
        thumb_w = self.config['thumb_width']
        spacing = int(self.config['spacing'] * thumb_w / 400)

        sub_format = self.config.get('sub_format', '标准 36×24')
        sub_format_configs = {
            "标准 36×24": (36, 24, 8),
            "半格 18×24": (18, 24, 4),
            "方形 24×24": (24, 24, 5),
            "XPan 65×24": (65, 24, 14),
        }
        frame_w_mm, frame_h_mm, perfs_per_frame = sub_format_configs.get(sub_format, (36, 24, 8))
        scale_factor = thumb_w / frame_w_mm

        strip_h = int(35.0 * scale_factor)
        perf_center_offset_px = int((2.01 + 2.794/2.0) * scale_factor)
        frame_top_offset_px = int((35.0 - 24.0) / 2.0 * scale_factor)
        frame_h_px = int(frame_h_mm * scale_factor)
        frame_w_px = int(frame_w_mm * scale_factor)
        pitch_mm = self.engine.get_perf_pitch(
            self.engine.determine_perf_type(
                self.config.get('info_film', ''),
                self.config.get('perf_mode', 'Auto')
            )
        )
        pitch_px = int(pitch_mm * scale_factor)

        content_w = (cols * frame_w_px) + ((cols + 1) * spacing)
        side_margin = int(50 * thumb_w / 400)
        top_margin = int(25 * thumb_w / 400)
        total_w = content_w + (side_margin * 2) + int(100 * thumb_w / 400)
        bag_gap = int(50 * thumb_w / 400)

        pack_img_path = self.config.get('pack_image', '')
        pack_position = self.config.get('pack_position', 'left')
        pack_img = None
        if pack_img_path and os.path.exists(pack_img_path):
            try:
                pack_img = Image.open(pack_img_path).convert('RGB')
            except Exception:
                pass

        has_pack_stroke = self.config.get('pack_border_stroke', True)
        pack_border = max(2, int(2 * thumb_w / 400)) if has_pack_stroke else 0
        pack_gap = int(20 * thumb_w / 400)

        info_height, _, _, _, _, _, _, has_info = \
            self._compute_info_height(thumb_w, thumb_w / 400, pack_img)

        info_to_film_gap = int(65 * thumb_w / 400)
        top_area_height = top_margin + info_height + info_to_film_gap
        top_region_height = top_margin + info_height
        bottom_margin = int(top_region_height * 2.0) if info_height == 0 else int(top_region_height * 1.6)
        total_h = int(top_area_height + (rows * strip_h) + ((rows - 1) * bag_gap) + bottom_margin)

        render_style = self.config.get('render_style', 'lightbox')
        colors = STYLE_COLORS.get(render_style, STYLE_COLORS["lightbox"])

        canvas = Image.new('RGB', (total_w, total_h), colors["canvas_bg"])
        draw = ImageDraw.Draw(canvas)

        text_area_left = side_margin
        text_area_right = total_w - side_margin
        if pack_img and info_height > 0:
            orig_w, orig_h = pack_img.size
            pack_size_pct = self.config.get('pack_size', 80)
            if isinstance(pack_size_pct, str):
                try:
                    pack_size_pct = int(pack_size_pct)
                except ValueError:
                    pack_size_pct = 80
            top_blank_height = top_margin + info_height + info_to_film_gap
            pack_h_display = min(int(top_blank_height * pack_size_pct / 100.0), 100)
            pack_w_display = int(pack_h_display * (orig_w / orig_h))
            if pack_w_display > int(total_w * 0.35):
                pack_w_display = int(total_w * 0.35)
                pack_h_display = int(pack_w_display * (orig_h / orig_w))
            if pack_w_display > 0 and pack_h_display > 0:
                resized_pack = pack_img.resize((pack_w_display, pack_h_display), Image.Resampling.LANCZOS)
                pack_y = (top_blank_height - pack_h_display) // 2
                if pack_position == 'left':
                    pack_x = side_margin
                    if has_pack_stroke:
                        pb = pack_border
                        draw.rectangle([pack_x - pb, pack_y - pb, pack_x + pack_w_display + pb, pack_y + pack_h_display + pb],
                                       outline=colors["pack_border"], width=pb)
                    canvas.paste(resized_pack, (pack_x, pack_y))
                    text_area_left = pack_x + pack_w_display + pack_gap
                else:
                    pack_x = total_w - side_margin - pack_w_display
                    if has_pack_stroke:
                        pb = pack_border
                        draw.rectangle([pack_x - pb, pack_y - pb, pack_x + pack_w_display + pb, pack_y + pack_h_display + pb],
                                       outline=colors["pack_border"], width=pb)
                    canvas.paste(resized_pack, (pack_x, pack_y))
                    text_area_right = pack_x - pack_gap

        if has_info:
            font_main = self._load_font(int(34 * thumb_w / 400))
            if font_main:
                self._draw_info_block(draw, font_main, colors, text_area_left, text_area_right,
                                      top_margin, int(20 * thumb_w / 400), int(52 * thumb_w / 400),
                                      thumb_w / 400.0, thumb_w)

        edge_text = self._generate_edge_text()
        film_base = colors["film_base"]
        perf_fill = colors["perf_fill"]
        perf_type = self.engine.determine_perf_type(
            self.config.get('info_film', ''), self.config.get('perf_mode', 'Auto')
        )

        perf_h = int(2.794 * scale_factor)
        perf_w = int(1.981 * scale_factor) if perf_type == "KS" else int(1.854 * scale_factor)

        img_idx = 0
        for row in range(rows):
            y1 = int(top_area_height + row * (strip_h + bag_gap))
            y2 = y1 + strip_h
            draw.rectangle([0, y1, total_w, y2], fill=film_base)

            for px in range(25, total_w - 25, int(pitch_px)):
                draw.rectangle([px - perf_w//2, y1 + perf_center_offset_px - perf_h//2,
                                px + perf_w//2, y1 + perf_center_offset_px + perf_h//2], fill=perf_fill)
                draw.rectangle([px - perf_w//2, y2 - perf_center_offset_px - perf_h//2,
                                px + perf_w//2, y2 - perf_center_offset_px + perf_h//2], fill=perf_fill)

            edge_font = self._load_font(int(14 * thumb_w / 400 * 0.85))
            if edge_font:
                draw.text((total_w // 2, y1 + 10), edge_text, fill=colors["text_color"], font=edge_font, anchor="mm")
                draw.text((total_w // 2, y2 - 10), edge_text, fill=colors["text_color"], font=edge_font, anchor="mm")

            start_col = 2 if row == 0 else 0
            for col in range(start_col, cols):
                if img_idx >= len(images):
                    break
                x_pos = side_margin + spacing + col * (frame_w_px + spacing)
                y_img_top = y1 + frame_top_offset_px
                big_img = self.cover_resize_crop(images[img_idx], frame_w_px, frame_h_px)
                canvas.paste(big_img, (int(x_pos), int(y_img_top)))
                img_idx += 1

        return canvas

    def _render_preview_120(self, images):
        """轻量级 120 预览渲染，无 AA，无保存，无文件夹打开。"""
        sub_format = self.config.get('sub_format', '66')
        target_ratio = FILM_FORMAT_RATIOS.get(sub_format, 1.0)

        cols = self.config['columns']
        rows = math.ceil(len(images) / cols)
        thumb_w = self.config['thumb_width']
        spacing = int(self.config['spacing'] * thumb_w / 400)
        base_scale = thumb_w / 400.0

        content_w = (cols * thumb_w) + ((cols + 1) * spacing)
        side_margin = int(50 * base_scale)
        top_margin = int(25 * base_scale)
        total_w = content_w + (side_margin * 2) + int(100 * base_scale)

        fixed_h = int(thumb_w / target_ratio)
        row_h = fixed_h + (spacing * 2)
        strip_h = int(25 * base_scale) + row_h + int(25 * base_scale)
        bag_gap = int(50 * base_scale)

        pack_img_path = self.config.get('pack_image', '')
        pack_position = self.config.get('pack_position', 'left')
        pack_img = None
        if pack_img_path and os.path.exists(pack_img_path):
            try:
                pack_img = Image.open(pack_img_path).convert('RGB')
            except Exception:
                pass

        has_pack_stroke = self.config.get('pack_border_stroke', True)
        pack_border = max(2, int(2 * base_scale)) if has_pack_stroke else 0
        pack_gap = int(20 * base_scale)

        info_height, _, _, _, _, _, _, has_info = \
            self._compute_info_height(thumb_w, base_scale, pack_img)

        info_to_film_gap = int(65 * base_scale)
        top_area_height = top_margin + info_height + info_to_film_gap
        top_region_height = top_margin + info_height
        bottom_margin = int(top_region_height * 2.0) if info_height == 0 else int(top_region_height * 1.6)
        total_h = int(top_area_height + (rows * strip_h) + ((rows - 1) * bag_gap) + bottom_margin)

        render_style = self.config.get('render_style', 'lightbox')
        colors = STYLE_COLORS.get(render_style, STYLE_COLORS["lightbox"])

        canvas = Image.new('RGB', (total_w, total_h), colors["canvas_bg"])
        draw = ImageDraw.Draw(canvas)

        text_area_left = side_margin
        text_area_right = total_w - side_margin
        if pack_img and info_height > 0:
            orig_w, orig_h = pack_img.size
            pack_size_pct = self.config.get('pack_size', 80)
            if isinstance(pack_size_pct, str):
                try:
                    pack_size_pct = int(pack_size_pct)
                except ValueError:
                    pack_size_pct = 80
            top_blank_height = top_margin + info_height + info_to_film_gap
            pack_h_display = min(int(top_blank_height * pack_size_pct / 100.0), 100)
            pack_w_display = int(pack_h_display * (orig_w / orig_h))
            if pack_w_display > int(total_w * 0.35):
                pack_w_display = int(total_w * 0.35)
                pack_h_display = int(pack_w_display * (orig_h / orig_w))
            if pack_w_display > 0 and pack_h_display > 0:
                resized_pack = pack_img.resize((pack_w_display, pack_h_display), Image.Resampling.LANCZOS)
                pack_y = (top_blank_height - pack_h_display) // 2
                if pack_position == 'left':
                    pack_x = side_margin
                    if has_pack_stroke:
                        pb = pack_border
                        draw.rectangle([pack_x - pb, pack_y - pb, pack_x + pack_w_display + pb, pack_y + pack_h_display + pb],
                                       outline=colors["pack_border"], width=pb)
                    canvas.paste(resized_pack, (pack_x, pack_y))
                    text_area_left = pack_x + pack_w_display + pack_gap
                else:
                    pack_x = total_w - side_margin - pack_w_display
                    if has_pack_stroke:
                        pb = pack_border
                        draw.rectangle([pack_x - pb, pack_y - pb, pack_x + pack_w_display + pb, pack_y + pack_h_display + pb],
                                       outline=colors["pack_border"], width=pb)
                    canvas.paste(resized_pack, (pack_x, pack_y))
                    text_area_right = pack_x - pack_gap

        if has_info:
            font_main = self._load_font(int(34 * base_scale))
            if font_main:
                self._draw_info_block(draw, font_main, colors, text_area_left, text_area_right,
                                      top_margin, int(20 * base_scale), int(52 * base_scale),
                                      base_scale, thumb_w)

        edge_text = self._generate_edge_text()
        film_base = colors["film_base"]
        img_idx = 0
        for row in range(rows):
            y1 = int(top_area_height + row * (strip_h + bag_gap))
            y2 = y1 + strip_h
            draw.rectangle([0, y1, total_w, y2], fill=film_base)

            edge_font = self._load_font(int(14 * base_scale * 0.85))
            if edge_font:
                draw.text((total_w // 2, y1 + 5), edge_text, fill=colors["text_color"], font=edge_font, anchor="mm")
                draw.text((total_w // 2, y2 - 5), edge_text, fill=colors["text_color"], font=edge_font, anchor="mm")

            for col in range(cols):
                if img_idx >= len(images):
                    break
                x_pos = side_margin + spacing + col * (thumb_w + spacing)
                y_img_top = y1 + int(25 * base_scale) + spacing
                canvas.paste(images[img_idx], (int(x_pos), int(y_img_top)))
                img_idx += 1

        return canvas
        sub_format = self.config.get('sub_format', '66')
        target_ratio = FILM_FORMAT_RATIOS.get(sub_format, 1.0)

        cols = self.config['columns']
        rows = math.ceil(len(images) / cols)
        thumb_w = self.config['thumb_width']
        spacing = int(self.config['spacing'] * thumb_w / 400)
        base_scale = thumb_w / 400.0

        content_w = (cols * thumb_w) + ((cols + 1) * spacing)
        side_margin = int(50 * base_scale)
        top_margin = int(25 * base_scale)
        total_w = content_w + (side_margin * 2) + int(100 * base_scale)

        fixed_h = int(thumb_w / target_ratio)
        row_h = fixed_h + (spacing * 2)
        strip_h = int(25 * base_scale) + row_h + int(25 * base_scale)
        bag_gap = int(50 * base_scale)

        # ---- 包装图 ----
        pack_img_path = self.config.get('pack_image', '')
        pack_position = self.config.get('pack_position', 'left')
        pack_img = None
        if pack_img_path and os.path.exists(pack_img_path):
            try:
                pack_img = Image.open(pack_img_path).convert('RGB')
            except Exception:
                pack_img = None

        has_pack_stroke = self.config.get('pack_border_stroke', True)
        pack_border = max(2, int(2 * base_scale)) if has_pack_stroke else 0
        pack_gap = int(20 * base_scale)
        safe_padding = int(15 * base_scale)

        # 共享 helper：计算 info 区域高度
        info_height, info_font_size, info_line_height, info_top_padding, info_bottom_padding, info_data, label_idx, has_info = \
            self._compute_info_height(thumb_w, base_scale, pack_img)

        info_to_film_gap = int(65 * base_scale)
        top_area_height = top_margin + info_height + info_to_film_gap
        top_region_height = top_margin + info_height
        if info_height == 0:
            bottom_margin = int(top_region_height * 2.0)
        else:
            bottom_margin = int(top_region_height * 1.6)
        total_h = int(top_area_height + (rows * strip_h) + ((rows - 1) * bag_gap) + bottom_margin)

        render_style = self.config.get('render_style', 'lightbox')
        colors = STYLE_COLORS.get(render_style, STYLE_COLORS["lightbox"])

        # ---- 4x 抗锯齿放大 ----
        aa_scale = 4
        self._aa_scale = aa_scale
        big_total_w = total_w * aa_scale
        big_total_h = total_h * aa_scale
        big_canvas = Image.new('RGB', (big_total_w, big_total_h), colors["canvas_bg"])
        big_draw = ImageDraw.Draw(big_canvas)

        big_current_y = top_area_height * aa_scale
        big_side_margin = side_margin * aa_scale
        big_spacing = spacing * aa_scale
        big_strip_h = strip_h * aa_scale
        big_bag_gap = bag_gap * aa_scale
        big_fixed_h = fixed_h * aa_scale
        big_thumb_w = thumb_w * aa_scale

        # 边字字体大小缩放到85%
        big_edge_font_sz = int(14 * base_scale * 0.85 * aa_scale)

        text_area_left = big_side_margin
        text_area_right = big_total_w - big_side_margin

        # ---- 包装图（放大画布上） ----
        if pack_img and info_height > 0:
            orig_w, orig_h = pack_img.size
            pack_size_pct = self.config.get('pack_size', 80)
            if isinstance(pack_size_pct, str):
                try:
                    pack_size_pct = int(pack_size_pct)
                except ValueError:
                    pack_size_pct = 80
            top_blank_height = top_margin + info_height + info_to_film_gap
            pack_h_display = int(top_blank_height * pack_size_pct / 100.0)
            if pack_h_display < 20:
                pack_h_display = 20
            pack_w_display = int(pack_h_display * (orig_w / orig_h))
            max_allow_w = int(total_w * 0.35)
            if pack_w_display > max_allow_w:
                pack_w_display = max_allow_w
                pack_h_display = int(pack_w_display * (orig_h / orig_w))
            if pack_w_display > 0 and pack_h_display > 0:
                big_pack = pack_img.resize(
                    (pack_w_display * aa_scale, pack_h_display * aa_scale),
                    Image.Resampling.LANCZOS
                )
                pack_y = (top_blank_height - pack_h_display) // 2 * aa_scale
                if pack_position == 'left':
                    pack_x = big_side_margin
                    if has_pack_stroke:
                        pb = pack_border * aa_scale
                        big_draw.rectangle([
                            pack_x - pb, pack_y - pb,
                            pack_x + pack_w_display * aa_scale + pb,
                            pack_y + pack_h_display * aa_scale + pb
                        ], outline=colors["pack_border"], width=pb)
                    big_canvas.paste(big_pack, (pack_x, pack_y))
                    text_area_left = pack_x + pack_w_display * aa_scale + pack_gap * aa_scale
                else:
                    pack_x = big_total_w - big_side_margin - pack_w_display * aa_scale
                    if has_pack_stroke:
                        pb = pack_border * aa_scale
                        big_draw.rectangle([
                            pack_x - pb, pack_y - pb,
                            pack_x + pack_w_display * aa_scale + pb,
                            pack_y + pack_h_display * aa_scale + pb
                        ], outline=colors["pack_border"], width=pb)
                    big_canvas.paste(big_pack, (pack_x, pack_y))
                    text_area_right = pack_x - pack_gap * aa_scale

        # ---- 拍摄信息 ----
        if has_info:
            font_main = self._load_font(info_font_size * aa_scale)
            if font_main:
                self._draw_info_block(
                    big_draw, font_main, colors,
                    text_area_left, text_area_right,
                    top_margin * aa_scale, info_top_padding * aa_scale,
                    info_line_height * aa_scale, base_scale, thumb_w
                )

        # ---- 边字 ----
        edge_text = self._generate_edge_text()
        film_base = colors["film_base"]

        img_idx = 0
        for row in range(rows):
            if self.is_cancelled:
                return "已取消"

            y1 = int(big_current_y)
            y2 = int(big_current_y + big_strip_h)
            big_draw.rectangle([0, y1, big_total_w, y2], fill=film_base)

            big_y_img_top = big_current_y + int(25 * base_scale * aa_scale) + spacing * aa_scale

            # Use a per-render seed so positions vary between runs but stay stable within one render
            render_seed = int(time.time()) ^ (row * 7919 + img_idx)
            rng = random.Random(render_seed)
            num_occurrences = rng.choice([2, 3, 4])

            margin = int(40 * base_scale * aa_scale)
            if num_occurrences == 2:
                base_ratios = [0.25, 0.75]
            elif num_occurrences == 3:
                base_ratios = [0.18, 0.50, 0.82]
            else:
                base_ratios = [0.12, 0.38, 0.62, 0.88]

            offset_range = int(0.08 * big_total_w)
            selected_x = []
            for ratio in base_ratios:
                base_x = margin + int(ratio * (big_total_w - 2*margin))
                offset = rng.randint(-offset_range, offset_range)
                x = max(margin, min(big_total_w - margin, base_x + offset))
                selected_x.append(x)
            selected_x.sort()

            # 120 边字Y坐标：距离片基边缘 5 * base_scale（靠近边缘）
            edge_y_offset = int(5 * base_scale * aa_scale)
            edge_y_top = big_current_y + edge_y_offset
            edge_y_bottom = big_current_y + big_strip_h - edge_y_offset

            font = self._load_font(big_edge_font_sz)
            if font:
                color = colors["text_color"]
                for x_pos in selected_x:
                    big_draw.text((x_pos, edge_y_top), edge_text, fill=color, font=font, anchor="mm")
                    big_draw.text((x_pos, edge_y_bottom), edge_text, fill=color, font=font, anchor="mm")

            # ---- 放置图片 ----
            for col in range(cols):
                if img_idx >= len(images):
                    break
                x_pos = big_side_margin + big_spacing + col * (big_thumb_w + big_spacing)
                big_img = self.cover_resize_crop(images[img_idx], big_thumb_w, big_fixed_h)
                big_canvas.paste(big_img, (int(x_pos), int(big_y_img_top)))
                img_idx += 1

            big_current_y += big_strip_h + big_bag_gap
            progress_callback(50 + int((row + 1) / rows * 50), f"行: {row+1}/{rows}")

        # ---- 缩回原尺寸（抗锯齿） ----
        status_callback("正在应用抗锯齿...")
        canvas = big_canvas.resize((total_w, total_h), Image.Resampling.LANCZOS)

        out_path = self.config['output_path']
        if out_path.lower().endswith('.png'):
            canvas.save(out_path, compress_level=1)
        else:
            canvas.save(out_path, quality=self.config['quality'], optimize=True)

        open_folder(out_path)
        return "success"