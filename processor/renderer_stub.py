# -*- coding: utf-8 -*-
"""Minimal processor shim for API/headless use.

Provides the interface that BaseRenderer expects from a FilmProcessor,
without file I/O, image processing, or UI dependencies.
"""

from PIL import ImageFont

from engine.film_engine import Strict135FilmEngine
from utils.helpers import (
    LABEL_MAP, INFO_LAYOUT, NO_COLON_FIELDS, get_system_font,
)
from .edge_text import generate_edge_text
from .image_pipeline import cover_resize_crop as _cover_resize_crop


class FilmProcessorStub:
    """Minimal processor shim for API/headless rendering."""

    is_cancelled = False
    _pack_img_original = None
    _size_cache: dict = {}
    _aa_scale: int = 1

    def __init__(self, config):
        self.config = config
        self.engine = Strict135FilmEngine(dpi=300)
        # Font cache: (size, family) -> font object
        self._font_cache: dict[tuple[int, str | None], ImageFont.FreeTypeFont | None] = {}

    def _load_font(self, size: int, family: str | None = None) -> ImageFont.FreeTypeFont | None:
        """Load font with LRU-style caching by (size, family)."""
        key = (size, family)
        if key not in self._font_cache:
            self._font_cache[key] = get_system_font(size)
        return self._font_cache[key]

    def _draw_info_block(
        self, draw, font_main, colors, text_area_left, text_area_right,
        top_margin, info_top_padding, info_line_height, base_scale, thumb_w,
    ):
        """Render the info labels + values on the canvas."""
        lang = self.config.get("info_lang", "en")
        label_idx = 0 if lang == "zh" else 1
        info_data = {key: self.config.get(f"info_{key}", "") for key in LABEL_MAP}

        col_gap = int(40 * thumb_w / 400) if thumb_w > 200 else int(40 * base_scale)
        num_cols = max(len(row) for row in INFO_LAYOUT)
        slot_widths = [0] * num_cols

        measured_texts: dict[str, int] = {}

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
                val = info_data.get(key, "")
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

        rendered_row = 0
        for r_idx, row_keys in enumerate(INFO_LAYOUT):
            if not any(info_data.get(k, "") for k in row_keys if k):
                continue
            abs_y = top_margin + info_top_padding + rendered_row * info_line_height
            abs_x = text_area_left
            for col_idx, key in enumerate(row_keys):
                if key is None:
                    abs_x += slot_widths[col_idx]
                    continue
                lbl = LABEL_MAP[key][label_idx]
                val = info_data.get(key, "")
                if key in NO_COLON_FIELDS:
                    draw.text((abs_x, abs_y), lbl, fill=colors["info_label_color"], font=font_main)
                    if val:
                        lbl_bbox = draw.textbbox((0, 0), lbl, font=font_main)
                        val_x = abs_x + (lbl_bbox[2] - lbl_bbox[0]) + (
                            int(8 * thumb_w / 400) if thumb_w > 200 else int(8 * base_scale)
                        )
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

    def _draw_triangle(self, draw, cx, cy, size, color):
        """Draw a left-pointing isosceles triangle with 30° apex angle."""
        import math
        half_angle_rad = math.radians(15)
        tri_w = size * math.cos(half_angle_rad)
        tri_h = size * math.sin(half_angle_rad)
        pts = [
            (cx - tri_w, cy),
            (cx + tri_w * 0.1, cy - tri_h),
            (cx + tri_w * 0.1, cy + tri_h),
        ]
        draw.polygon(pts, fill=color)

    def _generate_edge_text(self):
        """Build edge text from info_film field or use custom text."""
        custom = self.config.get("edge_text", "").strip()
        info_film = self.config.get("info_film", "").strip()
        return generate_edge_text(info_film, custom)

    cover_resize_crop = staticmethod(_cover_resize_crop)

    def _save_output(self, canvas):
        """No-op for API — renderer calls this but we return the canvas directly."""
