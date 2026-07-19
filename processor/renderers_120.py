# -*- coding: utf-8 -*-
"""120 film strip renderer — no perforations, configurable aspect ratios.

Physical model (ISO 732):
  - Backing paper width: 61mm (fixed) → strip_h in software
  - Image height on strip: 56mm (fixed, centered)
  - Image width varies by sub-format → thumb_w maps to this
  - Top/bottom border: (61 - 56) / 2 = 2.5mm each side

Software orientation: film strip is horizontal, so "strip height" = real backing paper width.
"""

import math

from .renderer import BaseRenderer
from utils.helpers import FILM_FORMAT_RATIOS


# Real image width in mm for each sub-format (height is always 56mm)
IMAGE_WIDTHS_MM = {
    "645": 41.5,
    "66":  56.0,
    "67":  67.0,
    "68":  34.5,
    "69":  41.5,
    "612": 24.0,
    "617": 15.2,
}

BACKING_WIDTH_MM = 61.0
IMAGE_HEIGHT_MM = 56.0
IMAGE_BORDER_TOP_BOTTOM_MM = (BACKING_WIDTH_MM - IMAGE_HEIGHT_MM) / 2  # 2.5mm each


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

        # Scale factor: thumb_w maps to the real image width in mm
        img_w_mm = IMAGE_WIDTHS_MM.get(sub_format, 56.0)
        scale_factor = thumb_w / img_w_mm

        # Strip height = backing paper width (61mm), image height = 56mm
        strip_h = int(BACKING_WIDTH_MM * scale_factor)
        img_h = int(IMAGE_HEIGHT_MM * scale_factor)

        # Bag gap between rows
        bag_gap = int(50 * scale_factor)

        base_scale = thumb_w / 400.0

        common = self._calc_common_layout(
            thumb_w, spacing, cols, rows, strip_h, bag_gap, base_scale)

        common.update({
            'target_ratio': target_ratio,
            'img_w_mm': img_w_mm,
            'img_h': img_h,
            'scale_factor': scale_factor,
            'image_border': int(IMAGE_BORDER_TOP_BOTTOM_MM * scale_factor),
            'base_scale': base_scale,
        })
        return common

    def draw_strip_decoration(self, draw, layout, row, y1, y2, img_idx, aa_scale=1):
        """120 strips have no perforations — edge text centered in the 2.5mm border area."""
        thumb_w = layout['thumb_w']
        total_w = layout['big_total_w']
        scale_factor = layout['scale_factor']
        aa_scale = layout.get('aa_scale', 1)

        edge_info = self.processor._generate_edge_text()
        font_size = int(16 * thumb_w / 400) * aa_scale
        font = self.processor._load_font(font_size)
        if not font:
            return
        color = self.colors["text_color"]

        # --- Top edge: centered, brand + film type ---
        border_mid = int(0.9 * scale_factor * aa_scale)
        top_parts = [edge_info["brand"]]
        if edge_info["film_type"]:
            top_parts.append(edge_info["film_type"])
        top_line = "  ".join(top_parts)
        draw.text((total_w // 2, y1 + border_mid), top_line, fill=color, font=font, anchor="mm")

        # --- Bottom edge: fixed at image centers + separator triangles ---
        edge_y_bottom = y2 - border_mid

        side_margin = layout['side_margin'] * aa_scale
        spacing = layout['spacing'] * aa_scale
        frame_w = thumb_w * aa_scale
        cols = layout['cols']

        start_col = 2 if row == 0 else 0
        image_centers = []
        cur_img = img_idx
        for c in range(start_col, cols):
            if cur_img >= len(self.images):
                break
            cx = side_margin + spacing + c * (frame_w + spacing) + frame_w // 2
            image_centers.append((cx, cur_img + 1))
            cur_img += 1

        # Draw numbered text at each image center
        for cx, num in image_centers:
            num_str = str(num)
            num_font = self.processor._load_font(font_size)
            if num_font:
                bbox = draw.textbbox((0, 0), num_str, font=num_font)
                num_w = bbox[2] - bbox[0]
                draw.text((cx - num_w // 2, edge_y_bottom), num_str, fill=color, font=num_font, anchor="mm")
                # Triangle right after the number, proportional to number width
                gap = 25
                self.processor._draw_triangle(
                    draw, cx - num_w // 2 + num_w + gap, edge_y_bottom, font_size * 0.7, color
                )

        # Draw separators between adjacent images
        for i in range(len(image_centers) - 1):
            sep_x = (image_centers[i][0] + image_centers[i + 1][0]) // 2
            self.processor._draw_triangle(
                draw, sep_x, edge_y_bottom, font_size * 0.6, color
            )

    def _place_images_in_row(self, canvas, layout, row, y1, y2, img_idx, scale):
        """Place images for a 120 strip row."""
        side_margin = layout['side_margin'] * scale
        spacing = layout['spacing'] * scale
        thumb_w = layout['thumb_w'] * scale
        img_h = layout['img_h'] * scale
        image_border = layout['image_border'] * scale

        start_col = 2 if row == 0 else 0
        for col in range(start_col, layout['cols']):
            if img_idx >= len(self.images):
                break
            x_pos = side_margin + spacing + col * (thumb_w + spacing)
            # Center image vertically on the strip (2.5mm border top/bottom)
            y_img_top = y1 + image_border
            placed_img = self.processor.cover_resize_crop(
                self.images[img_idx], thumb_w, img_h)
            canvas.paste(placed_img, (int(x_pos), int(y_img_top)))
            img_idx += 1
