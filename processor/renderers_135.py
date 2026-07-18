# -*- coding: utf-8 -*-
"""135 film strip renderer — supports perforations, sub-formats, edge text."""

import math
import random
import time

from .renderer import BaseRenderer
from engine.film_engine import Strict135FilmEngine


class Renderer135(BaseRenderer):
    """Render a 135 film strip layout with physical perforations and edge text."""

    FORMAT_NAME = "135"

    def __init__(self, config, processor, images, status_callback=None,
                 progress_callback=None, is_preview=False):
        super().__init__(config, processor, images, status_callback,
                         progress_callback, is_preview)
        # Sub-format configs: (width_mm, height_mm, perforations_per_frame)
        self.sub_format_configs = {
            "标准 36×24": (36, 24, 8),
            "半格 18×24": (18, 24, 4),
            "方形 24×24": (24, 24, 5),
            "XPan 65×24": (65, 24, 14),
        }

    def compute_layout(self):
        thumb_w = self.config['thumb_width']
        spacing = int(self.config['spacing'] * thumb_w / 400)
        cols = self.config['columns']
        rows = math.ceil(len(self.images) / cols)

        sub_format = self.config.get('sub_format', '标准 36×24')
        frame_w_mm, frame_h_mm, perfs_per_frame = self.sub_format_configs.get(
            sub_format, (36, 24, 8))

        scale_factor = thumb_w / frame_w_mm
        strip_h = int(35.0 * scale_factor)
        bag_gap = int(50 * thumb_w / 400)

        base_scale = thumb_w / 400.0
        common = self._calc_common_layout(
            thumb_w, spacing, cols, rows, strip_h, bag_gap, base_scale)

        # 135-specific layout values
        common.update({
            'frame_w_mm': frame_w_mm, 'frame_h_mm': frame_h_mm,
            'perfs_per_frame': perfs_per_frame,
            'scale_factor': scale_factor,
            'strip_h': strip_h, 'bag_gap': bag_gap,
            'base_scale': base_scale,
            # Perforation dimensions
            'perf_center_offset_px': int((2.01 + 2.794 / 2.0) * scale_factor),
            'frame_top_offset_px': int((35.0 - 24.0) / 2.0 * scale_factor),
            'frame_h_px': int(frame_h_mm * scale_factor),
            'frame_w_px': int(frame_w_mm * scale_factor),
            'perf_h_px': int(2.794 * scale_factor),
            'perf_w_ks_px': int(1.981 * scale_factor),
            'perf_w_bh_px': int(1.854 * scale_factor),
            'perf_r_px': int(0.508 * scale_factor),
            'bh_cd_px': int(0.35 * scale_factor),
            'pitch_px': int(self.engine.get_perf_pitch(
                self.engine.determine_perf_type(
                    self.config.get('info_film', ''),
                    self.config.get('perf_mode', 'Auto'))
            ) * scale_factor),
        })
        return common

    def draw_strip_decoration(self, draw, layout, row, y1, y2, img_idx, aa_scale=1):
        """Draw perforations and edge text for a 135 strip row."""
        scale = layout.get('aa_scale', 1)
        base_scale = layout['base_scale']
        thumb_w = layout['thumb_w']
        total_w = layout['big_total_w']

        # Draw perforations
        self._draw_perforations(draw, layout, y1, y2, scale)

        # Edge text
        edge_text = self.processor._generate_edge_text()
        font_size = int(14 * thumb_w / 400 * 0.85) * scale
        font = self.processor._load_font(font_size)
        if not font:
            return

        color = self.colors["text_color"]
        margin = int(40 * base_scale) * scale
        offset_range = int(0.08 * total_w)

        # Random edge text positions
        render_seed = int(time.time()) ^ (row * 7919 + img_idx)
        rng = random.Random(render_seed)
        num_occurrences = rng.choice([2, 3, 4])

        if num_occurrences == 2:
            base_ratios = [0.25, 0.75]
        elif num_occurrences == 3:
            base_ratios = [0.18, 0.50, 0.82]
        else:
            base_ratios = [0.12, 0.38, 0.62, 0.88]

        selected_x = []
        for ratio in base_ratios:
            base_x = margin + int(ratio * (total_w - 2 * margin))
            offset = rng.randint(-offset_range, offset_range)
            x = max(margin, min(total_w - margin, base_x + offset))
            selected_x.append(x)
        selected_x.sort()

        edge_y_offset = int(10 * thumb_w / 400) * scale
        edge_y_top = y1 + edge_y_offset
        edge_y_bottom = y2 - edge_y_offset

        for x_pos in selected_x:
            draw.text((x_pos, edge_y_top), edge_text, fill=color, font=font, anchor="mm")
            draw.text((x_pos, edge_y_bottom), edge_text, fill=color, font=font, anchor="mm")

    def _draw_perforations(self, draw, layout, y1, y2, scale):
        """Draw perforations along top and bottom of a strip."""
        perf_fill = self.colors["perf_fill"]
        perf_type = self.engine.determine_perf_type(
            self.config.get('info_film', ''),
            self.config.get('perf_mode', 'Auto'))

        pitch_px = layout['pitch_px'] * scale
        perf_center_offset = layout['perf_center_offset_px'] * scale
        perf_h = layout['perf_h_px'] * scale

        if perf_type == "KS":
            perf_w = layout['perf_w_ks_px'] * scale
            perf_r = layout['perf_r_px'] * scale
            for x in range(25 * scale, layout['big_total_w'] - 25 * scale, int(pitch_px)):
                for cy in (y1 + perf_center_offset, y2 - perf_center_offset):
                    draw.rounded_rectangle(
                        [x - perf_w // 2, cy - perf_h // 2,
                         x + perf_w // 2, cy + perf_h // 2],
                        radius=perf_r, fill=perf_fill)
        else:
            perf_w = layout['perf_w_bh_px'] * scale
            cd = layout['bh_cd_px'] * scale
            for x in range(25 * scale, layout['big_total_w'] - 25 * scale, int(pitch_px)):
                for cy in (y1 + perf_center_offset, y2 - perf_center_offset):
                    draw.rectangle(
                        [x - perf_w // 2, cy - perf_h // 2 + cd,
                         x + perf_w // 2, cy + perf_h // 2 - cd],
                        fill=perf_fill)
                    draw.ellipse(
                        [x - perf_w // 2, cy - perf_h // 2,
                         x + perf_w // 2, cy - perf_h // 2 + 2 * cd],
                        fill=perf_fill)
                    draw.ellipse(
                        [x - perf_w // 2, cy + perf_h // 2 - 2 * cd,
                         x + perf_w // 2, cy + perf_h // 2],
                        fill=perf_fill)

    def _place_images_in_row(self, canvas, layout, row, y1, y2, img_idx, scale):
        """Place images for a 135 strip row."""
        side_margin = layout['side_margin'] * scale
        spacing = layout['spacing'] * scale
        frame_w = layout['frame_w_px'] * scale
        frame_h = layout['frame_h_px'] * scale
        frame_top_offset = layout['frame_top_offset_px'] * scale
        thumb_w = layout['thumb_w'] * scale

        start_col = 2 if row == 0 else 0
        for col in range(start_col, layout['cols']):
            if img_idx >= len(self.images):
                break
            x_pos = side_margin + spacing + col * (frame_w + spacing)
            y_img_top = y1 + frame_top_offset
            placed_img = self.processor.cover_resize_crop(
                self.images[img_idx], frame_w, frame_h)
            canvas.paste(placed_img, (int(x_pos), int(y_img_top)))
            img_idx += 1

    @property
    def engine(self):
        return self.processor.engine
