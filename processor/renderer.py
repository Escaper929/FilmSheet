# -*- coding: utf-8 -*-
"""Shared rendering infrastructure for 135 and 120 film strips."""

import math
import os
from PIL import Image, ImageDraw, ImageFont

from utils.helpers import STYLE_COLORS, LABEL_MAP, INFO_LAYOUT, NO_COLON_FIELDS, open_folder


class BaseRenderer:
    """Abstract base class for film strip rendering.

    Subclasses implement format-specific layout parameters and strip decoration
    (perforations, edge text, etc.) while sharing common logic for canvas
    creation, info block rendering, pack image placement, and image tiling.
    """

    # Override in subclass
    FORMAT_NAME = "unknown"
    AA_SCALE = 4

    def __init__(self, config, processor, images, status_callback=None,
                 progress_callback=None, is_preview=False):
        self.config = config
        self.processor = processor
        self.images = images
        self.status_callback = status_callback or (lambda _: None)
        self.progress_callback = progress_callback or (lambda *_: None)
        self.is_preview = is_preview
        self.colors = self._resolve_colors()

    # ------------------------------------------------------------------
    # Public entry point
    # ------------------------------------------------------------------

    def render(self):
        """Render the full film sheet and return a PIL Image.

        For preview mode returns the image directly.
        For production mode saves to disk and returns "success" / error string.
        """
        layout = self.compute_layout()
        canvas, draw, layout = self._build_canvas(layout)
        self._draw_pack_image(canvas, layout)
        self._draw_info_block(canvas, layout)
        result = self._draw_strips(canvas, layout)
        if result == "已取消":
            return "已取消"
        self._draw_watermark(canvas, layout)
        canvas = self._downscale_if_aa(canvas, layout)

        if self.is_preview:
            return canvas

        self._save_output(canvas)
        return "success"

    # ------------------------------------------------------------------
    # Abstract — must implement in subclass
    # ------------------------------------------------------------------

    def compute_layout(self):
        """Return a dict of layout parameters.

        Required keys (examples):
            cols, rows, thumb_w, spacing, base_scale, total_w, total_h,
            strip_h, bag_gap, side_margin, top_margin, bottom_margin,
            top_area_height, frame_w_px, frame_h_px, frame_top_offset_px,
            info_height, info_font_size, info_line_height,
            info_top_padding, info_bottom_padding, has_info,
            pack_img, pack_position, pack_size_pct, has_pack_stroke,
            pack_border, pack_gap, colors
        """
        raise NotImplementedError

    def draw_strip_decoration(self, draw, layout, row, y1, y2, img_idx, aa_scale=1):
        """Draw format-specific strip decorations (perforations, edge text).

        Called once per row on the (possibly scaled) canvas.
        """
        pass

    # ------------------------------------------------------------------
    # Shared helpers (used by subclasses and render pipeline)
    # ------------------------------------------------------------------

    def _resolve_colors(self):
        render_style = self.config.get('render_style', 'lightbox')
        return STYLE_COLORS.get(render_style, STYLE_COLORS["lightbox"])

    def _status(self, msg):
        self.status_callback(msg)

    def _progress(self, val, msg):
        self.progress_callback(val, msg)

    # -- Layout computations (shared) --------------------------------

    def _calc_common_layout(self, thumb_w, spacing, cols, rows, strip_h, bag_gap,
                            base_scale=None):
        """Compute layout values shared across formats.

        Returns a dict with common keys.
        """
        if base_scale is None:
            base_scale = thumb_w / 400.0

        side_margin = int(50 * base_scale)
        top_margin = int(25 * base_scale)
        content_w = (cols * thumb_w) + ((cols + 1) * spacing)
        total_w = content_w + (side_margin * 2) + int(100 * base_scale)

        pack_img = self._load_pack_image()
        has_info = self._has_info()
        info_height = self._compute_info_height(thumb_w, base_scale, pack_img)

        info_to_film_gap = int(65 * base_scale)
        top_area_height = top_margin + info_height + info_to_film_gap
        top_region_height = top_margin + info_height
        bottom_margin = (int(top_region_height * 2.0) if info_height == 0
                         else int(top_region_height * 1.6))
        total_h = int(top_area_height + (rows * strip_h) +
                      ((rows - 1) * bag_gap) + bottom_margin)

        return {
            'cols': cols, 'rows': rows, 'thumb_w': thumb_w,
            'spacing': spacing, 'base_scale': base_scale,
            'side_margin': side_margin, 'top_margin': top_margin,
            'bottom_margin': bottom_margin,
            'content_w': content_w, 'total_w': total_w, 'total_h': total_h,
            'bag_gap': bag_gap, 'strip_h': strip_h,
            'top_area_height': top_area_height,
            'info_height': info_height,
            'pack_img': pack_img,
        }

    def _load_pack_image(self):
        pack_img_path = self.config.get('pack_image', '')
        if pack_img_path and os.path.exists(pack_img_path):
            try:
                return Image.open(pack_img_path).convert('RGB')
            except Exception:
                pass
        return None

    def _has_info(self):
        return any(
            v for row in INFO_LAYOUT
            for k in row if k and (v := self.config.get(f'info_{k}', ''))
        )

    def _compute_info_height(self, thumb_w, base_scale, pack_img):
        has_info = self._has_info()
        info_font_size = int(34 * thumb_w / 400) if thumb_w > 200 else int(34 * base_scale)
        info_line_height = int(52 * thumb_w / 400) if thumb_w > 200 else int(52 * base_scale)
        info_top_padding = int(20 * thumb_w / 400) if thumb_w > 200 else int(20 * base_scale)
        info_bottom_padding = int(15 * thumb_w / 400) if thumb_w > 200 else int(15 * base_scale)

        lang = self.config.get('info_lang', 'en')
        label_idx = 0 if lang == 'zh' else 1
        info_data = {key: self.config.get(f'info_{key}', '') for key in LABEL_MAP}
        active_rows = sum(1 for row in INFO_LAYOUT
                          if any(info_data.get(k, '') for k in row if k))

        ih = 0
        if has_info and active_rows > 0:
            ih = info_top_padding + active_rows * info_line_height + info_bottom_padding
        if pack_img and ih == 0:
            ih = int(140 * thumb_w / 400) if thumb_w > 200 else int(140 * base_scale)
        return ih

    # -- Canvas construction -----------------------------------------

    def _build_canvas(self, layout):
        aa_scale = self.AA_SCALE if not self.is_preview else 1
        total_w = layout['total_w']
        total_h = layout['total_h']

        if aa_scale > 1:
            self.processor._aa_scale = aa_scale
            big_total_w = total_w * aa_scale
            big_total_h = total_h * aa_scale
            canvas = Image.new('RGB', (big_total_w, big_total_h),
                               self.colors["canvas_bg"])
        else:
            canvas = Image.new('RGB', (total_w, total_h), self.colors["canvas_bg"])

        draw = ImageDraw.Draw(canvas)

        # Store scaled dimensions back into layout for convenience
        layout['aa_scale'] = aa_scale
        layout['big_total_w'] = canvas.size[0]
        layout['big_total_h'] = canvas.size[1]
        layout['scaled'] = aa_scale > 1
        layout['canvas'] = canvas
        layout['draw'] = draw

        return canvas, draw, layout

    def _downscale_if_aa(self, canvas, layout):
        aa_scale = layout.get('aa_scale', 1)
        if aa_scale > 1 and not self.is_preview:
            self._status("正在应用抗锯齿...")
            orig_w, orig_h = layout['big_total_w'], layout['big_total_h']
            target_w = orig_w // aa_scale
            target_h = orig_h // aa_scale
            return canvas.resize((target_w, target_h), Image.Resampling.LANCZOS)
        return canvas

    # -- Pack image --------------------------------------------------

    def _draw_pack_image(self, canvas, layout):
        pack_img = layout.get('pack_img')
        if not pack_img:
            return

        pack_position = self.config.get('pack_position', 'left')
        pack_size_pct = self.config.get('pack_size', 80)
        if isinstance(pack_size_pct, str):
            try:
                pack_size_pct = int(pack_size_pct)
            except ValueError:
                pack_size_pct = 80

        base_scale = layout['base_scale']
        info_height = layout['info_height']
        top_margin = layout['top_margin']
        top_area_height = layout['top_area_height']
        total_w = layout['total_w']
        side_margin = layout['side_margin']
        spacing = layout['spacing']
        aa_scale = layout.get('aa_scale', 1)

        if info_height == 0:
            return  # no room for pack image

        top_blank_height = top_margin + info_height + int(65 * base_scale)
        pack_h_display = min(int(top_blank_height * pack_size_pct / 100.0), 100)
        orig_w, orig_h = pack_img.size
        pack_w_display = int(pack_h_display * (orig_w / orig_h))
        max_allow_w = int(total_w * 0.35)
        if pack_w_display > max_allow_w:
            pack_w_display = max_allow_w
            pack_h_display = int(pack_w_display * (orig_h / orig_w))

        if pack_w_display <= 0 or pack_h_display <= 0:
            return

        pack_resized = pack_img.resize(
            (pack_w_display * aa_scale, pack_h_display * aa_scale),
            Image.Resampling.LANCZOS
        )
        pack_y = (top_blank_height - pack_h_display) // 2 * aa_scale

        has_pack_stroke = self.config.get('pack_border_stroke', True)
        pack_border = max(2, int(2 * base_scale)) if has_pack_stroke else 0

        draw = layout.get('draw')
        # Track pack bounds for text area clamping
        if pack_position == 'left':
            pack_x = side_margin * aa_scale
            if has_pack_stroke:
                pb = pack_border * aa_scale
                draw.rectangle([
                    pack_x - pb, pack_y - pb,
                    pack_x + pack_w_display * aa_scale + pb,
                    pack_y + pack_h_display * aa_scale + pb
                ], outline=self.colors["pack_border"], width=pb)
            canvas.paste(pack_resized, (pack_x, pack_y))
            # Right edge of pack (+ gap), aa-scale coords — stored for _draw_info_block
            layout['_pi_right'] = pack_x + pack_w_display * aa_scale + int(12 * aa_scale)
        else:
            pack_x = (total_w * aa_scale) - side_margin * aa_scale - pack_w_display * aa_scale
            if has_pack_stroke:
                pb = pack_border * aa_scale
                draw.rectangle([
                    pack_x - pb, pack_y - pb,
                    pack_x + pack_w_display * aa_scale + pb,
                    pack_y + pack_h_display * aa_scale + pb
                ], outline=self.colors["pack_border"], width=pb)
            canvas.paste(pack_resized, (pack_x, pack_y))
            # Left edge of pack (- gap), aa-scale coords — stored for _draw_info_block
            layout['_pi_left'] = pack_x - int(12 * aa_scale)

    # -- Info block --------------------------------------------------

    def _draw_info_block(self, canvas, layout):
        if not layout.get('info_height', 0):
            return

        draw = layout['draw']
        aa_scale = layout.get('aa_scale', 1)
        base_scale = layout['base_scale']
        thumb_w = layout['thumb_w']
        side_margin = layout['side_margin'] * aa_scale
        total_w = layout['big_total_w']

        font_main = self.processor._load_font(
            int(34 * thumb_w / 400) * aa_scale
        )
        if not font_main:
            return

        # Clamp text area to avoid overlapping with pack image
        text_left = side_margin
        text_right = total_w - side_margin
        pi_right = layout.get('_pi_right')   # pack on left → clamp text start
        pi_left = layout.get('_pi_left')     # pack on right → clamp text end
        if pi_right is not None:
            text_left = max(text_left, pi_right)
        if pi_left is not None:
            text_right = min(text_right, pi_left)

        self.processor._draw_info_block(
            draw, font_main, self.colors,
            text_left, text_right,
            layout['top_margin'] * aa_scale,
            int(20 * thumb_w / 400) * aa_scale,
            int(52 * thumb_w / 400) * aa_scale,
            base_scale, thumb_w
        )

    # -- Strip rows --------------------------------------------------

    def _draw_strips(self, canvas, layout):
        draw = layout['draw']
        aa_scale = layout.get('aa_scale', 1)
        rows = layout['rows']
        cols = layout['cols']
        strip_h = layout['strip_h'] * aa_scale
        bag_gap = layout['bag_gap'] * aa_scale
        top_area_height = layout['top_area_height'] * aa_scale
        side_margin = layout['side_margin'] * aa_scale
        spacing = layout['spacing'] * aa_scale
        thumb_w = layout['thumb_w'] * aa_scale
        total_w = layout['big_total_w']
        film_base = self.colors["film_base"]

        img_idx = 0
        for row in range(rows):
            if not self.is_preview and self.processor.is_cancelled:
                return "已取消"

            y1 = int(top_area_height + row * (strip_h + bag_gap))
            y2 = y1 + strip_h
            draw.rectangle([0, y1, total_w, y2], fill=film_base)

            # Format-specific decoration (perforations, edge text, etc.)
            self.draw_strip_decoration(draw, layout, row, y1, y2, img_idx, aa_scale)

            # Place images in this row
            self._place_images_in_row(canvas, layout, row, y1, y2, img_idx, aa_scale)
            img_idx += cols

            if not self.is_preview:
                self._progress(50 + int((row + 1) / rows * 50),
                               f"渲染行: {row+1}/{rows}")

    def _place_images_in_row(self, canvas, layout, row, y1, y2, img_idx, aa_scale):
        """Place images for one row. Override in subclass for custom placement."""
        raise NotImplementedError

    # -- Output ------------------------------------------------------

    def _save_output(self, canvas):
        from utils.helpers import open_folder
        self._status("正在保存文件...")
        out_path = self.config['output_path']
        if out_path.lower().endswith('.png'):
            canvas.save(out_path, compress_level=1)
        else:
            canvas.save(out_path, quality=self.config['quality'], optimize=True)
        open_folder(out_path)

    # -- Watermark ---------------------------------------------------

    def _draw_watermark(self, canvas, layout):
        """Draw signature watermark at bottom-right of the canvas."""
        sig = self.config.get('signature', '').strip()
        if not sig:
            return

        draw = layout['draw']
        aa_scale = layout['aa_scale']
        total_w = layout['big_total_w']
        total_h = layout['big_total_h']
        base_scale = layout['base_scale']

        font_size = int(18 * layout['thumb_w'] / 400) * aa_scale
        font = self.processor._load_font(font_size)
        if not font:
            return

        margin = int(30 * base_scale) * aa_scale
        bbox = draw.textbbox((0, 0), sig, font=font)
        text_w = bbox[2] - bbox[0]
        text_h = bbox[3] - bbox[1]

        x = total_w - margin - text_w
        y = total_h - margin - text_h

        # Clamp to avoid overflowing canvas
        if x < margin:
            x = margin
        if y < margin:
            y = margin

        color = self.colors.get("text_color", (255, 255, 255))
        shadow_color = self.colors.get("text_shadow", (0, 0, 0))

        # Shadow for readability on any background
        draw.text((x + 1, y + 1), sig, fill=shadow_color, font=font, anchor="lt")
        draw.text((x, y), sig, fill=color, font=font, anchor="lt")
