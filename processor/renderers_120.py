# -*- coding: utf-8 -*-
"""120 film strip renderer — no perforations, configurable aspect ratios."""

import math

from .renderer import BaseRenderer
from utils.helpers import FILM_FORMAT_RATIOS


class Renderer120(BaseRenderer):
    """Render a 120 film strip layout with configurable sub-formats."""

    FORMAT_NAME = "120"

    def compute_layout(self):
        thumb_w = self.config['thumb_width']
        spacing = int(self.config['spacing'] * thumb_w / 400)
        cols = self.config['columns']
        rows = math.ceil(len(self.images) / cols)

        sub_format = self.config.get('sub_format', '66')
        target_ratio = FILM_FORMAT_RATIOS.get(sub_format, 1.0)
        fixed_h = int(thumb_w / target_ratio)

        base_scale = thumb_w / 400.0
        row_h = fixed_h + (spacing * 2)
        strip_h = int(25 * base_scale) + row_h + int(25 * base_scale)
        bag_gap = int(50 * base_scale)

        common = self._calc_common_layout(
            thumb_w, spacing, cols, rows, strip_h, bag_gap, base_scale)

        common.update({
            'target_ratio': target_ratio,
            'fixed_h': fixed_h,
            'row_h': row_h,
            'base_scale': base_scale,
        })
        return common

    def draw_strip_decoration(self, draw, layout, row, y1, y2, img_idx, aa_scale=1):
        """120 strips have no perforations — just center edge text."""
        thumb_w = layout['thumb_w']
        total_w = layout['big_total_w']
        font_size = int(14 * thumb_w / 400 * 0.85) * aa_scale
        font = self.processor._load_font(font_size)
        if not font:
            return
        edge_text = self.processor._generate_edge_text()
        draw.text((total_w // 2, y1 + 10), edge_text,
                  fill=self.colors["text_color"], font=font, anchor="mm")
        draw.text((total_w // 2, y2 - 10), edge_text,
                  fill=self.colors["text_color"], font=font, anchor="mm")

    def _place_images_in_row(self, canvas, layout, row, y1, y2, img_idx, scale):
        """Place images for a 120 strip row."""
        side_margin = layout['side_margin'] * scale
        spacing = layout['spacing'] * scale
        thumb_w = layout['thumb_w'] * scale
        fixed_h = layout['fixed_h'] * scale

        start_col = 2 if row == 0 else 0
        for col in range(start_col, layout['cols']):
            if img_idx >= len(self.images):
                break
            x_pos = side_margin + spacing + col * (thumb_w + spacing)
            y_img_top = y1 + int(25 * layout['base_scale'] * scale) + spacing * scale
            placed_img = self.processor.cover_resize_crop(
                self.images[img_idx], thumb_w, fixed_h)
            canvas.paste(placed_img, (int(x_pos), int(y_img_top)))
            img_idx += 1
