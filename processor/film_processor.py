# -*- coding: utf-8 -*-

import os
import sys
import math
import random
import json
import threading
import subprocess
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

    def cancel(self):
        self.is_cancelled = True

    def draw_edge_text_at(self, draw, text, x, y, font_size, style="lightbox"):
        if not text:
            return
        font = get_system_font(font_size)
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
                with ThreadPoolExecutor(max_workers=4) as executor:
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
                with ThreadPoolExecutor(max_workers=4) as executor:
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
        """使用物理级 135 引擎渲染（4x 抗锯齿，所有尺寸基于 thumb_w 等比缩放）"""
        cols = self.config['columns']
        rows = math.ceil(len(images) / cols)
        thumb_w = self.config['thumb_width']
        spacing = int(self.config['spacing'] * thumb_w / 400)

        # 抗锯齿缩放系数
        aa_scale = 4

        # ===== 所有尺寸基于 thumb_w 等比缩放（比例因子 = thumb_w / 36.0） =====
        scale_factor = thumb_w / 36.0

        # 胶片条高度 = 35mm 对应的像素
        strip_h = int(35.0 * scale_factor)
        # 齿孔中心偏移 = (2.01 + 2.794/2) mm 对应的像素
        perf_center_offset_px = int((2.01 + 2.794/2.0) * scale_factor)
        # 成像区域上边缘 = (35 - 24) / 2 mm 对应的像素
        frame_top_offset_px = int((35.0 - 24.0) / 2.0 * scale_factor)
        # 成像区域高度 = 24mm 对应的像素
        frame_h_px = int(24.0 * scale_factor)

        # 齿孔尺寸
        perf_h_px = int(2.794 * scale_factor)      # 齿孔高度
        perf_w_ks_px = int(1.981 * scale_factor)   # KS 齿孔宽度
        perf_w_bh_px = int(1.854 * scale_factor)   # BH 齿孔宽度
        perf_r_px = int(0.508 * scale_factor)      # 齿孔圆角半径
        bh_cd_px = int(0.35 * scale_factor)        # BH 圆弧深度
        pitch_px = int(4.75 * scale_factor)        # 齿孔间距（近似）

        # 布局计算
        content_w = (cols * thumb_w) + ((cols + 1) * spacing)
        side_margin = int(50 * thumb_w / 400)
        top_margin = int(25 * thumb_w / 400)
        total_w = content_w + (side_margin * 2) + int(100 * thumb_w / 400)
        bag_gap = int(50 * thumb_w / 400)

        # ---- 信息区域 ----
        lang = self.config.get('info_lang', 'en')
        label_idx = 0 if lang == 'zh' else 1
        info_data = {key: self.config.get(f'info_{key}', '') for key in LABEL_MAP}
        has_info = any(v for v in info_data.values())

        info_font_size = int(34 * thumb_w / 400)
        info_line_height = int(52 * thumb_w / 400)
        info_top_padding = int(20 * thumb_w / 400)
        info_bottom_padding = int(15 * thumb_w / 400)
        active_rows = sum(1 for row in INFO_LAYOUT if any(info_data.get(k, '') for k in row if k))
        info_height = 0
        if has_info and active_rows > 0:
            info_height = info_top_padding + active_rows * info_line_height + info_bottom_padding

        # ---- 包装图 ----
        pack_img_path = self.config.get('pack_image', '')
        pack_position = self.config.get('pack_position', 'left')
        pack_img = None
        if pack_img_path and os.path.exists(pack_img_path):
            try:
                pack_img = Image.open(pack_img_path).convert('RGB')
            except Exception:
                pack_img = None

        if pack_img and info_height == 0:
            info_height = int(140 * thumb_w / 400)

        has_pack_stroke = self.config.get('pack_border_stroke', True)
        pack_border = max(2, int(2 * thumb_w / 400)) if has_pack_stroke else 0
        pack_gap = int(20 * thumb_w / 400)
        safe_padding = int(15 * thumb_w / 400)
        max_pack_height = info_height - 2 * safe_padding - 2 * pack_border

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
        big_thumb_w = thumb_w * aa_scale
        big_strip_h = strip_h * aa_scale
        big_bag_gap = bag_gap * aa_scale
        big_frame_h_px = frame_h_px * aa_scale
        big_perf_center_offset_px = perf_center_offset_px * aa_scale
        big_frame_top_offset_px = frame_top_offset_px * aa_scale
        big_pitch_px = pitch_px * aa_scale

        # 大画布上的齿孔参数
        big_perf_h_px = perf_h_px * aa_scale
        big_perf_w_ks_px = perf_w_ks_px * aa_scale
        big_perf_w_bh_px = perf_w_bh_px * aa_scale
        big_perf_r_px = perf_r_px * aa_scale
        big_bh_cd_px = bh_cd_px * aa_scale

        # ---- 边字字体大小：基础值缩放到85%，再放大 ----
        base_font = int(14 * thumb_w / 400) if thumb_w > 200 else 14
        big_edge_font_sz = int(base_font * 0.85) * aa_scale

        # ---- 在放大画布上绘制包装图 ----
        big_text_area_left = big_side_margin
        big_text_area_right = big_total_w - big_side_margin

        if pack_img and info_height > 0:
            orig_w, orig_h = pack_img.size

            # 直接从配置文件读取滑块值
            from utils.helpers import load_config
            cfg = load_config()
            pack_size = cfg.get('pack_size', 80)

            top_blank_height = top_margin + info_height + info_to_film_gap
            pack_h_display = int(top_blank_height * pack_size / 100.0)
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
                        border_rect = [
                            pack_x - pack_border * aa_scale,
                            pack_y - pack_border * aa_scale,
                            pack_x + pack_w_display * aa_scale + pack_border * aa_scale,
                            pack_y + pack_h_display * aa_scale + pack_border * aa_scale
                        ]
                        big_draw.rectangle(border_rect, outline=colors["pack_border"], width=pack_border * aa_scale)
                    big_canvas.paste(big_pack, (pack_x, pack_y))
                    big_text_area_left = pack_x + pack_w_display * aa_scale + pack_gap * aa_scale
                else:
                    pack_x = big_total_w - big_side_margin - pack_w_display * aa_scale
                    if has_pack_stroke:
                        border_rect = [
                            pack_x - pack_border * aa_scale,
                            pack_y - pack_border * aa_scale,
                            pack_x + pack_w_display * aa_scale + pack_border * aa_scale,
                            pack_y + pack_h_display * aa_scale + pack_border * aa_scale
                        ]
                        big_draw.rectangle(border_rect, outline=colors["pack_border"], width=pack_border * aa_scale)
                    big_canvas.paste(big_pack, (pack_x, pack_y))
                    big_text_area_right = pack_x - pack_gap * aa_scale

        # ---- 大画布上的齿孔绘制函数 ----
        def draw_perf_big(draw, cx, cy, perf_fill, perf_type):
            """在放大画布上绘制齿孔"""
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
            font_main = get_system_font(info_font_size * aa_scale)
            if font_main:
                label_color = colors["info_label_color"]
                value_color = colors["info_text_color"]
                col_gap = int(40 * thumb_w / 400) * aa_scale
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
                        bbox = big_draw.textbbox((0, 0), full_text, font=font_main)
                        text_w = bbox[2] - bbox[0]
                        slot_widths[col_idx] = max(slot_widths[col_idx], text_w + col_gap)
                total_slot_w = sum(slot_widths)
                available_w = big_text_area_right - big_text_area_left
                if total_slot_w > available_w and total_slot_w > 0:
                    scale_factor2 = available_w / total_slot_w
                    slot_widths = [int(sw * scale_factor2) for sw in slot_widths]
                rendered_row = 0
                for r_idx, row_keys in enumerate(INFO_LAYOUT):
                    if not any(info_data.get(k, '') for k in row_keys if k):
                        continue
                    abs_y = top_margin * aa_scale + info_top_padding * aa_scale + rendered_row * info_line_height * aa_scale
                    abs_x = big_text_area_left
                    for col_idx, key in enumerate(row_keys):
                        if key is None:
                            abs_x += slot_widths[col_idx]
                            continue
                        lbl = LABEL_MAP[key][label_idx]
                        val = info_data.get(key, '')
                        if key in NO_COLON_FIELDS:
                            label_str = lbl
                            big_draw.text((abs_x, abs_y), label_str, fill=label_color, font=font_main)
                            if val:
                                lbl_bbox = big_draw.textbbox((0, 0), label_str, font=font_main)
                                val_x = abs_x + (lbl_bbox[2] - lbl_bbox[0]) + int(8 * thumb_w / 400) * aa_scale
                                big_draw.text((val_x, abs_y), val, fill=value_color, font=font_main)
                        else:
                            label_str = f"{lbl}: "
                            big_draw.text((abs_x, abs_y), label_str, fill=label_color, font=font_main)
                            if val:
                                lbl_bbox = big_draw.textbbox((0, 0), label_str, font=font_main)
                                val_x = abs_x + (lbl_bbox[2] - lbl_bbox[0])
                                big_draw.text((val_x, abs_y), val, fill=value_color, font=font_main)
                        abs_x += slot_widths[col_idx]
                    rendered_row += 1

        # ---- 齿孔类型 ----
        perf_type = self.engine.determine_perf_type(
            self.config.get('info_film', ''),
            self.config.get('perf_mode', 'Auto')
        )

        # ---- 绘制胶片条（在放大画布上） ----
        film_base = colors["film_base"]
        perf_fill = colors["perf_fill"]

        big_img_idx = 0
        custom_edge_text = self.config.get('edge_text', '')

        # 自动生成边字内容（从“胶卷”字段）
        info_film = self.config.get('info_film', '')
        parts = info_film.split()
        brand = parts[0].upper() if parts else "KODAK"
        film_type = parts[1] if len(parts) > 1 else "5207"

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

            # ---- 边字（135模式：距离片基边缘 10 * base_scale，字体缩放到85%） ----
            if custom_edge_text:
                edge_text = custom_edge_text
            else:
                edge_text = f"{brand}  {film_type} ◀"

            # 确定本行边字出现次数（2-4）
            random.seed(row * 1000 + big_img_idx)
            num_occurrences = random.choice([2, 3, 4])

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
                offset = random.randint(-offset_range, offset_range)
                x = max(margin, min(big_total_w - margin, base_x + offset))
                selected_x.append(x)
            selected_x.sort()

            # 135 边字Y坐标：距离片基边缘 10 * base_scale
            edge_y_offset = int(10 * thumb_w / 400) * aa_scale
            edge_y_top = big_current_y + edge_y_offset
            edge_y_bottom = big_current_y + big_strip_h - edge_y_offset

            font = get_system_font(big_edge_font_sz)
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
                x_pos = big_side_margin + big_spacing + col * (big_thumb_w + big_spacing)
                img_original = images[big_img_idx]
                big_img = img_original.resize(
                    (img_original.width * aa_scale, img_original.height * aa_scale),
                    Image.Resampling.LANCZOS
                )
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

    def _render_120(self, images, status_callback, progress_callback):
        # 确保 target_ratio 已定义
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

        # 信息区域
        lang = self.config.get('info_lang', 'en')
        label_idx = 0 if lang == 'zh' else 1
        info_data = {key: self.config.get(f'info_{key}', '') for key in LABEL_MAP}
        has_info = any(v for v in info_data.values())

        info_font_size = int(34 * base_scale)
        info_line_height = int(52 * base_scale)
        info_top_padding = int(20 * base_scale)
        info_bottom_padding = int(15 * base_scale)
        active_rows = sum(1 for row in INFO_LAYOUT if any(info_data.get(k, '') for k in row if k))
        info_height = 0
        if has_info and active_rows > 0:
            info_height = info_top_padding + active_rows * info_line_height + info_bottom_padding

        # 包装图
        pack_img_path = self.config.get('pack_image', '')
        pack_position = self.config.get('pack_position', 'left')
        pack_img = None
        if pack_img_path and os.path.exists(pack_img_path):
            try:
                pack_img = Image.open(pack_img_path).convert('RGB')
            except Exception:
                pack_img = None

        if pack_img and info_height == 0:
            info_height = int(140 * base_scale)

        has_pack_stroke = self.config.get('pack_border_stroke', True)
        pack_border = max(2, int(2 * base_scale)) if has_pack_stroke else 0
        pack_gap = int(20 * base_scale)
        safe_padding = int(15 * base_scale)
        max_pack_height = info_height - 2 * safe_padding - 2 * pack_border

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

        canvas = Image.new('RGB', (total_w, total_h), colors["canvas_bg"])
        draw = ImageDraw.Draw(canvas)

        current_y = top_area_height
        img_idx = 0

        # ---- 120 边字字体大小缩放到85% ----
        edge_font_sz = int(14 * base_scale * 0.85)
        custom_edge_text = self.config.get('edge_text', '')

        # 自动生成边字内容（从“胶卷”字段）
        info_film = self.config.get('info_film', '')
        parts = info_film.split()
        brand = parts[0].upper() if parts else "KODAK"
        film_type = parts[1] if len(parts) > 1 else "120"

        text_area_left = side_margin
        text_area_right = total_w - side_margin

        # 包装图
        if pack_img and info_height > 0 and max_pack_height > 0:
            orig_w, orig_h = pack_img.size
            pack_h_display = max_pack_height
            pack_w_display = int(pack_h_display * (orig_w / orig_h))
            max_allow_w = int(total_w * 0.3)
            if pack_w_display > max_allow_w:
                pack_w_display = max_allow_w
                pack_h_display = int(pack_w_display * (orig_h / orig_w))
            if pack_w_display > 0 and pack_h_display > 0:
                resized_pack = pack_img.resize((pack_w_display, pack_h_display), Image.Resampling.LANCZOS)
                top_offset = top_margin
                available_space = info_height - 2 * safe_padding
                centered_offset = safe_padding + (available_space - pack_h_display) // 2
                pack_y = top_offset + centered_offset
                if pack_position == 'left':
                    pack_x = side_margin
                    if has_pack_stroke:
                        border_rect = [
                            pack_x - pack_border, pack_y - pack_border,
                            pack_x + pack_w_display + pack_border,
                            pack_y + pack_h_display + pack_border
                        ]
                        draw.rectangle(border_rect, outline=colors["pack_border"], width=pack_border)
                    canvas.paste(resized_pack, (pack_x, pack_y))
                    text_area_left = pack_x + pack_w_display + pack_gap
                else:
                    pack_x = total_w - side_margin - pack_w_display
                    if has_pack_stroke:
                        border_rect = [
                            pack_x - pack_border, pack_y - pack_border,
                            pack_x + pack_w_display + pack_border,
                            pack_y + pack_h_display + pack_border
                        ]
                        draw.rectangle(border_rect, outline=colors["pack_border"], width=pack_border)
                    canvas.paste(resized_pack, (pack_x, pack_y))
                    text_area_right = pack_x - pack_gap

        # 拍摄信息
        if has_info:
            font_main = get_system_font(info_font_size)
            if font_main:
                label_color = colors["info_label_color"]
                value_color = colors["info_text_color"]
                col_gap = int(40 * base_scale)
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
                            draw.text((abs_x, abs_y), label_str, fill=label_color, font=font_main)
                            if val:
                                lbl_bbox = draw.textbbox((0, 0), label_str, font=font_main)
                                val_x = abs_x + (lbl_bbox[2] - lbl_bbox[0]) + int(8 * base_scale)
                                draw.text((val_x, abs_y), val, fill=value_color, font=font_main)
                        else:
                            label_str = f"{lbl}: "
                            draw.text((abs_x, abs_y), label_str, fill=label_color, font=font_main)
                            if val:
                                lbl_bbox = draw.textbbox((0, 0), label_str, font=font_main)
                                val_x = abs_x + (lbl_bbox[2] - lbl_bbox[0])
                                draw.text((val_x, abs_y), val, fill=value_color, font=font_main)
                        abs_x += slot_widths[col_idx]
                    rendered_row += 1

        # ---- 绘制胶片条 ----
        film_base = colors["film_base"]

        for row in range(rows):
            if self.is_cancelled:
                return "已取消"

            draw.rectangle([0, int(current_y), total_w, int(current_y + strip_h)], fill=film_base)
            y_img_top = current_y + int(25 * base_scale) + spacing

            # ---- 边字（120模式：距离片基边缘 5 * base_scale，靠近边缘，字体85%） ----
            if custom_edge_text:
                edge_text = custom_edge_text
            else:
                edge_text = f"{brand}  {film_type} ◀"

            random.seed(row * 1000 + img_idx)
            num_occurrences = random.choice([2, 3, 4])

            margin = int(40 * base_scale)
            if num_occurrences == 2:
                base_ratios = [0.25, 0.75]
            elif num_occurrences == 3:
                base_ratios = [0.18, 0.50, 0.82]
            else:
                base_ratios = [0.12, 0.38, 0.62, 0.88]

            offset_range = int(0.08 * total_w)
            selected_x = []
            for ratio in base_ratios:
                base_x = margin + int(ratio * (total_w - 2*margin))
                offset = random.randint(-offset_range, offset_range)
                x = max(margin, min(total_w - margin, base_x + offset))
                selected_x.append(x)
            selected_x.sort()

            # 120 边字Y坐标：距离片基边缘 5 * base_scale（靠近边缘）
            edge_y_offset = int(9 * base_scale)
            edge_y_top = current_y + edge_y_offset
            edge_y_bottom = current_y + strip_h - edge_y_offset

            font = get_system_font(edge_font_sz)
            if font:
                color = colors["text_color"]
                for x_pos in selected_x:
                    draw.text((x_pos, edge_y_top), edge_text, fill=color, font=font, anchor="mm")
                    draw.text((x_pos, edge_y_bottom), edge_text, fill=color, font=font, anchor="mm")

            # ---- 放置图片 ----
            for col in range(cols):
                if img_idx >= len(images):
                    break
                x_pos = side_margin + spacing + col * (thumb_w + spacing)
                canvas.paste(images[img_idx], (int(x_pos), int(y_img_top)))
                img_idx += 1

            current_y += strip_h + bag_gap
            progress_callback(50 + int((row + 1) / rows * 50), f"行: {row+1}/{rows}")

        out_path = self.config['output_path']
        if out_path.lower().endswith('.png'):
            canvas.save(out_path, compress_level=1)
        else:
            canvas.save(out_path, quality=self.config['quality'], optimize=True)

        open_folder(out_path)
        return "success"