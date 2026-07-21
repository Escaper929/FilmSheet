# -*- coding: utf-8 -*-
"""FilmSheet REST API backend for NAS / cloud deployment.

Exposes a single POST /render endpoint that accepts images + config,
returns the rendered film sheet as a JPEG/PNG.

Designed to run on any Python host (FlueNas NAS, Docker, VPS, etc.).
Desktop and mobile apps share this same API.
"""

from __future__ import annotations

import io
import os
import sys
import math
import tempfile
from typing import Optional

# Add parent directory to path for imports
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from fastapi import FastAPI, UploadFile, Form
from fastapi.responses import Response
from PIL import Image, ImageDraw, ImageFont

from processor.config_schema import validate_config, sanitize_config, FIELD_DEFS
from processor.edge_text import generate_edge_text
from processor.image_pipeline import (
    process_135_image as _process_135_image,
    process_120_image as _process_120_image,
    cover_resize_crop,
)
from engine.film_engine import Strict135FilmEngine
from utils.helpers import (
    STYLE_COLORS, LABEL_MAP, INFO_LAYOUT, NO_COLON_FIELDS,
    FILM_FORMAT_RATIOS, SUPPORTED_FORMATS, get_system_font,
)

# ---------------------------------------------------------------------------
# App
# ---------------------------------------------------------------------------

app = FastAPI(
    title="FilmSheet API",
    description="胶片扫描排版渲染服务 — 把你的数码扫描件变成真实的灯箱灯板 / 接触印相作品。",
    version="1.5.0",
)

# Serve mobile web frontend
@app.get("/")
async def serve_web():
    from fastapi.responses import FileResponse
    return FileResponse(os.path.join(os.path.dirname(__file__), "index.html"))

# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _load_font_cached(size: int) -> Optional[ImageFont.FreeTypeFont]:
    """Simple font cache for API requests."""
    if not hasattr(_load_font_cached, "_cache"):
        _load_font_cached._cache: dict[int, Optional[ImageFont.FreeTypeFont]] = {}
    if size not in _load_font_cached._cache:
        _load_font_cached._cache[size] = get_system_font(size)
    return _load_font_cached._cache[size]


def _draw_info_block_api(
    draw: ImageDraw.ImageDraw,
    font_main: ImageFont.FreeTypeFont,
    colors: dict,
    text_area_left: int,
    text_area_right: int,
    top_margin: int,
    info_top_padding: int,
    info_line_height: int,
    thumb_w: int,
    config: dict,
):
    """API-friendly version of _draw_info_block (no self dependency)."""
    lang = config.get("info_lang", "en")
    label_idx = 0 if lang == "zh" else 1
    info_data = {key: config.get(f"info_{key}", "") for key in LABEL_MAP}

    col_gap = int(40 * thumb_w / 400)
    num_cols = max(len(row) for row in INFO_LAYOUT)
    slot_widths = [0] * num_cols
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
                label_str = lbl
                draw.text((abs_x, abs_y), label_str, fill=colors["info_label_color"], font=font_main)
                if val:
                    lbl_bbox = draw.textbbox((0, 0), label_str, font=font_main)
                    val_x = abs_x + (lbl_bbox[2] - lbl_bbox[0]) + int(8 * thumb_w / 400)
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


def _draw_triangle_api(
    draw: ImageDraw.ImageDraw,
    cx: int,
    cy: int,
    size: int,
    color: tuple,
):
    """Draw a 30° apex left-pointing isosceles triangle."""
    import math as _math
    half_angle_rad = _math.radians(15)
    tri_w = size * _math.cos(half_angle_rad)
    tri_h = size * _math.sin(half_angle_rad)
    pts = [
        (cx - tri_w, cy),
        (cx + tri_w * 0.1, cy - tri_h),
        (cx + tri_w * 0.1, cy + tri_h),
    ]
    draw.polygon(pts, fill=color)


def _draw_edge_text_api(
    draw: ImageDraw.ImageDraw,
    edge_info: dict,
    font: ImageFont.FreeTypeFont,
    color: tuple,
    x: int,
    y_top: int,
    y_bottom: int,
    img_num: Optional[int] = None,
    font_size: int = 0,
):
    """Draw structured edge text on a film strip."""
    if edge_info.get("custom"):
        draw.text((x, y_top), edge_info["brand"], fill=color, font=font, anchor="mm")
        draw.text((x, y_bottom), edge_info["brand"], fill=color, font=font, anchor="mm")
        return

    brand = edge_info["brand"]
    film_type = edge_info["film_type"]

    top_parts = [brand]
    if film_type:
        top_parts.append(film_type)
    top_line = "  ".join(top_parts)

    draw.text((x, y_top), top_line, fill=color, font=font, anchor="mm")

    if img_num is not None:
        num_str = str(img_num)
        draw.text((x, y_bottom), num_str, fill=color, font=font, anchor="mm")
        bbox = draw.textbbox((0, 0), num_str, font=font)
        num_w = bbox[2] - bbox[0]
        gap = 25
        _draw_triangle_api(draw, x + num_w + gap, y_bottom, font_size * 0.7, color)
    else:
        draw.text((x, y_bottom), "", fill=color, font=font, anchor="mm")


# ---------------------------------------------------------------------------
# Render 135 (API-only, no file I/O)
# ---------------------------------------------------------------------------

def _render_135_api(
    images: list[Image.Image],
    config: dict,
    is_preview: bool = False,
) -> Image.Image:
    """Render a 135 film sheet and return PIL Image (no file I/O)."""
    cols = config["columns"]
    rows = math.ceil(len(images) / cols)
    thumb_w = config["thumb_width"]
    spacing = config.get("spacing", 20)
    spacing = int(spacing * thumb_w / 400)

    aa_scale = 4 if not is_preview else 1

    sub_format = config.get("sub_format", "标准 36×24")
    sub_format_configs = {
        "标准 36×24": (36, 24, 8),
        "半格 18×24": (18, 24, 4),
        "方形 24×24": (24, 24, 5),
        "XPan 65×24": (65, 24, 14),
    }
    frame_w_mm, frame_h_mm, perfs_per_frame = sub_format_configs.get(sub_format, (36, 24, 8))
    scale_factor = thumb_w / frame_w_mm

    strip_h = int(35.0 * scale_factor)
    perf_center_offset_px = int((2.01 + 2.794 / 2.0) * scale_factor)
    frame_top_offset_px = int((35.0 - 24.0) / 2.0 * scale_factor)
    frame_h_px = int(frame_h_mm * scale_factor)
    frame_w_px = int(frame_w_mm * scale_factor)

    perf_h_px = int(2.794 * scale_factor)
    perf_w_ks_px = int(1.981 * scale_factor)
    perf_w_bh_px = int(1.854 * scale_factor)
    perf_r_px = int(0.508 * scale_factor)
    bh_cd_px = int(0.35 * scale_factor)
    pitch_mm = Strict135FilmEngine().get_perf_pitch(
        Strict135FilmEngine().determine_perf_type(config.get("info_film", ""), config.get("perf_mode", "Auto"))
    )
    pitch_px = int(pitch_mm * scale_factor)

    content_w = (cols * frame_w_px) + ((cols + 1) * spacing)
    side_margin = int(50 * thumb_w / 400)
    top_margin = int(25 * thumb_w / 400)
    total_w = content_w + (side_margin * 2) + int(100 * thumb_w / 400)
    bag_gap = int(50 * thumb_w / 400)

    # Pack image
    pack_img_path = config.get("pack_image", "")
    pack_img = None
    if pack_img_path and os.path.exists(pack_img_path):
        try:
            pack_img = Image.open(pack_img_path).convert("RGB")
        except Exception:
            pass

    has_pack_stroke = config.get("pack_border_stroke", True)
    pack_border = max(2, int(2 * thumb_w / 400)) if has_pack_stroke else 0
    pack_gap = int(20 * thumb_w / 400)

    # Info
    has_info = any(
        v for row in INFO_LAYOUT
        for k in row if k and (v := config.get(f"info_{k}", ""))
    )
    lang = config.get("info_lang", "en")
    label_idx = 0 if lang == "zh" else 1
    info_data = {key: config.get(f"info_{key}", "") for key in LABEL_MAP}
    active_rows = sum(1 for row in INFO_LAYOUT if any(info_data.get(k, "") for k in row if k))
    info_font_size = int(34 * thumb_w / 400) if thumb_w > 200 else int(34 * (thumb_w / 400.0))
    info_line_height = int(52 * thumb_w / 400) if thumb_w > 200 else int(52 * (thumb_w / 400.0))
    info_top_padding = int(20 * thumb_w / 400) if thumb_w > 200 else int(20 * (thumb_w / 400.0))
    info_bottom_padding = int(15 * thumb_w / 400) if thumb_w > 200 else int(15 * (thumb_w / 400.0))

    pack_position = config.get("pack_position", "left")
    pack_size_pct = config.get("pack_size", 80)
    if isinstance(pack_size_pct, str):
        try:
            pack_size_pct = int(pack_size_pct)
        except ValueError:
            pack_size_pct = 80

    info_height = 0
    if has_info and active_rows > 0:
        info_height = info_top_padding + active_rows * info_line_height + info_bottom_padding
    if pack_img and info_height == 0:
        info_height = int(140 * thumb_w / 400) if thumb_w > 200 else int(140 * (thumb_w / 400.0))

    info_to_film_gap = int(65 * thumb_w / 400)
    top_area_height = top_margin + info_height + info_to_film_gap
    top_region_height = top_margin + info_height
    bottom_margin = int(top_region_height * 2.0) if info_height == 0 else int(top_region_height * 1.6)
    total_h = int(top_area_height + (rows * strip_h) + ((rows - 1) * bag_gap) + bottom_margin)

    render_style = config.get("render_style", "lightbox")
    colors = STYLE_COLORS.get(render_style, STYLE_COLORS["lightbox"])

    big_total_w = total_w * aa_scale
    big_total_h = total_h * aa_scale
    big_canvas = Image.new("RGB", (big_total_w, big_total_h), colors["canvas_bg"])
    big_draw = ImageDraw.Draw(big_canvas)

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
    big_perf_h_px = perf_h_px * aa_scale
    big_perf_w_ks_px = perf_w_ks_px * aa_scale
    big_perf_w_bh_px = perf_w_bh_px * aa_scale
    big_perf_r_px = perf_r_px * aa_scale
    big_bh_cd_px = bh_cd_px * aa_scale

    edge_font_sz = int(16 * thumb_w / 400) * aa_scale
    info_font_sz = info_font_size * aa_scale

    # Pack image
    big_text_area_left = big_side_margin
    big_text_area_right = big_total_w - big_side_margin
    if pack_img and info_height > 0:
        orig_w, orig_h = pack_img.size
        top_blank_height = top_margin + info_height + info_to_film_gap
        pack_h_display = min(int(top_blank_height * pack_size_pct / 100.0), 100)
        pack_w_display = int(pack_h_display * (orig_w / orig_h))
        max_allow_w = int(total_w * 0.35)
        if pack_w_display > max_allow_w:
            pack_w_display = max_allow_w
            pack_h_display = int(pack_w_display * (orig_h / orig_w))
        if pack_w_display > 0 and pack_h_display > 0:
            big_pack = pack_img.resize((pack_w_display * aa_scale, pack_h_display * aa_scale), Image.Resampling.LANCZOS)
            pack_y = (top_blank_height - pack_h_display) // 2 * aa_scale
            if pack_position == "left":
                pack_x = big_side_margin
                if has_pack_stroke:
                    pb = pack_border * aa_scale
                    big_draw.rectangle([pack_x - pb, pack_y - pb, pack_x + pack_w_display * aa_scale + pb, pack_y + pack_h_display * aa_scale + pb],
                                       outline=colors["pack_border"], width=pb)
                big_canvas.paste(big_pack, (pack_x, pack_y))
                big_text_area_left = pack_x + pack_w_display * aa_scale + pack_gap * aa_scale
            else:
                pack_x = big_total_w - big_side_margin - pack_w_display * aa_scale
                if has_pack_stroke:
                    pb = pack_border * aa_scale
                    big_draw.rectangle([pack_x - pb, pack_y - pb, pack_x + pack_w_display * aa_scale + pb, pack_y + pack_h_display * aa_scale + pb],
                                       outline=colors["pack_border"], width=pb)
                big_canvas.paste(big_pack, (pack_x, pack_y))
                big_text_area_right = pack_x - pack_gap * aa_scale

    # Info block
    if has_info:
        font_main = _load_font_cached(info_font_sz)
        if font_main:
            _draw_info_block_api(
                big_draw, font_main, colors,
                big_text_area_left, big_text_area_right,
                top_margin * aa_scale, info_top_padding * aa_scale,
                info_line_height * aa_scale, thumb_w, config
            )

    # Edge text info
    edge_info = generate_edge_text(config.get("info_film", ""), config.get("edge_text", ""))

    film_base = colors["film_base"]
    perf_fill = colors["perf_fill"]
    perf_type = Strict135FilmEngine().determine_perf_type(
        config.get("info_film", ""), config.get("perf_mode", "Auto")
    )

    big_img_idx = 0
    for row in range(rows):
        y1 = int(big_current_y)
        y2 = int(big_current_y + big_strip_h)
        big_draw.rectangle([0, y1, big_total_w, y2], fill=film_base)

        big_perf_y_top = big_current_y + big_perf_center_offset_px
        big_perf_y_bottom = big_current_y + big_strip_h - big_perf_center_offset_px
        big_y_img_top = big_current_y + big_frame_top_offset_px

        # Perforations
        if perf_type == "KS":
            pw = big_perf_w_ks_px
            pr = big_perf_r_px
            for x in range(25 * aa_scale, big_total_w - 25 * aa_scale, int(big_pitch_px)):
                for cy in (big_perf_y_top, big_perf_y_bottom):
                    big_draw.rounded_rectangle([x - pw//2, cy - big_perf_h_px//2, x + pw//2, cy + big_perf_h_px//2],
                                               radius=pr, fill=perf_fill)
        else:
            pw = big_perf_w_bh_px
            cd = big_bh_cd_px
            for x in range(25 * aa_scale, big_total_w - 25 * aa_scale, int(big_pitch_px)):
                for cy in (big_perf_y_top, big_perf_y_bottom):
                    big_draw.rectangle([x - pw//2, cy - big_perf_h_px//2 + cd, x + pw//2, cy + big_perf_h_px//2 - cd], fill=perf_fill)
                    big_draw.ellipse([x - pw//2, cy - big_perf_h_px//2, x + pw//2, cy - big_perf_h_px//2 + 2*cd], fill=perf_fill)
                    big_draw.ellipse([x - pw//2, cy + big_perf_h_px//2 - 2*cd, x + pw//2, cy + big_perf_h_px//2], fill=perf_fill)

        # Edge text
        font = _load_font_cached(edge_font_sz)
        if font:
            color = colors["text_color"]
            margin = int(40 * thumb_w / 400) * aa_scale
            offset_range = int(0.08 * big_total_w)
            import random as _random
            import time as _time
            render_seed = int(_time.time()) ^ (row * 7919 + big_img_idx)
            rng = _random.Random(render_seed)
            num_occ = rng.choice([2, 3, 4])
            if num_occ == 2:
                base_ratios = [0.25, 0.75]
            elif num_occ == 3:
                base_ratios = [0.18, 0.50, 0.82]
            else:
                base_ratios = [0.12, 0.38, 0.62, 0.88]
            sel_x = []
            for ratio in base_ratios:
                bx = margin + int(ratio * (big_total_w - 2*margin))
                off = rng.randint(-offset_range, offset_range)
                sx = max(margin, min(big_total_w - margin, bx + off))
                sel_x.append(sx)
            sel_x.sort()
            edge_y_off = int(0.9 * scale_factor) * aa_scale
            ey_top = y1 + edge_y_off
            ey_bottom = y2 - edge_y_off
            for sx in sel_x:
                top_parts = [edge_info["brand"]]
                if edge_info["film_type"]:
                    top_parts.append(edge_info["film_type"])
                big_draw.text((sx, ey_top), "  ".join(top_parts), fill=color, font=font, anchor="mm")

        # Images
        start_col = 2 if row == 0 else 0
        for col in range(start_col, cols):
            if big_img_idx >= len(images):
                break
            x_pos = big_side_margin + big_spacing + col * (big_frame_w_px + big_spacing)
            big_img = cover_resize_crop(images[big_img_idx], big_frame_w_px, big_frame_h_px)
            big_canvas.paste(big_img, (int(x_pos), int(big_y_img_top)))
            big_img_idx += 1

        big_current_y += big_strip_h + big_bag_gap

    # Downscale if AA
    if not is_preview:
        canvas = big_canvas.resize((total_w, total_h), Image.Resampling.LANCZOS)
    else:
        canvas = big_canvas

    # Watermark
    sig = config.get("signature", "").strip()
    if sig:
        wm_font = _load_font_cached(int(18 * thumb_w / 400))
        if wm_font:
            margin = int(30 * thumb_w / 400)
            bbox = big_draw.textbbox((0, 0), sig, font=wm_font)
            tw = bbox[2] - bbox[0]
            th = bbox[3] - bbox[1]
            wx = total_w - margin - tw
            wy = total_h - margin - th
            big_draw.text((wx + 1, wy + 1), sig, fill=(0, 0, 0), font=wm_font, anchor="lt")
            big_draw.text((wx, wy), sig, fill=colors.get("text_color", (255, 255, 255)), font=wm_font, anchor="lt")

    return canvas


# ---------------------------------------------------------------------------
# Render 120 (API-only, no file I/O)
# ---------------------------------------------------------------------------

# 120 sub-format image widths in mm (height is always 56mm, backing paper 61mm)
_120_IMAGE_WIDTHS_MM = {
    "645": 41.5,
    "66":  56.0,
    "67":  67.0,
    "68":  34.5,
    "69":  41.5,
    "612": 24.0,
    "617": 15.2,
}

_BACKING_WIDTH_MM = 61.0
_IMAGE_HEIGHT_MM = 56.0
_IMAGE_BORDER_MM = (_BACKING_WIDTH_MM - _IMAGE_HEIGHT_MM) / 2  # 2.5mm each


def _render_120_api(
    images: list[Image.Image],
    config: dict,
    is_preview: bool = False,
) -> Image.Image:
    """Render a 120 film sheet and return PIL Image (no file I/O).

    120 strips have no perforations — only edge text and image borders.
    """
    cols = config["columns"]
    rows = math.ceil(len(images) / cols)
    thumb_w = config["thumb_width"]
    spacing = int(config.get("spacing", 20) * thumb_w / 400)

    aa_scale = 4 if not is_preview else 1

    sub_format = config.get("sub_format", "66")
    img_w_mm = _120_IMAGE_WIDTHS_MM.get(sub_format, 56.0)
    scale_factor = thumb_w / img_w_mm
    strip_h = int(_BACKING_WIDTH_MM * scale_factor)
    img_h = int(_IMAGE_HEIGHT_MM * scale_factor)
    image_border = int(_IMAGE_BORDER_MM * scale_factor)
    bag_gap = int(50 * scale_factor)

    base_scale = thumb_w / 400.0
    side_margin = int(50 * base_scale)
    top_margin = int(25 * base_scale)
    content_w = (cols * thumb_w) + ((cols + 1) * spacing)
    total_w = content_w + (side_margin * 2) + int(100 * base_scale)

    # Pack image
    pack_img_path = config.get("pack_image", "")
    pack_img = None
    if pack_img_path and os.path.exists(pack_img_path):
        try:
            pack_img = Image.open(pack_img_path).convert("RGB")
        except Exception:
            pass

    has_pack_stroke = config.get("pack_border_stroke", True)
    pack_border = max(2, int(2 * base_scale)) if has_pack_stroke else 0
    pack_gap = int(20 * base_scale)

    # Info
    has_info = any(
        v for row in INFO_LAYOUT
        for k in row if k and (v := config.get(f"info_{k}", ""))
    )
    lang = config.get("info_lang", "en")
    label_idx = 0 if lang == "zh" else 1
    info_data = {key: config.get(f"info_{key}", "") for key in LABEL_MAP}
    active_rows = sum(1 for row in INFO_LAYOUT if any(info_data.get(k, "") for k in row if k))
    info_font_size = int(34 * base_scale)
    info_line_height = int(52 * base_scale)
    info_top_padding = int(20 * base_scale)
    info_bottom_padding = int(15 * base_scale)

    pack_position = config.get("pack_position", "left")
    pack_size_pct = config.get("pack_size", 80)
    if isinstance(pack_size_pct, str):
        try:
            pack_size_pct = int(pack_size_pct)
        except ValueError:
            pack_size_pct = 80

    info_height = 0
    if has_info and active_rows > 0:
        info_height = info_top_padding + active_rows * info_line_height + info_bottom_padding
    if pack_img and info_height == 0:
        info_height = int(140 * base_scale)

    info_to_film_gap = int(65 * base_scale)
    top_area_height = top_margin + info_height + info_to_film_gap
    top_region_height = top_margin + info_height
    bottom_margin = int(top_region_height * 2.0) if info_height == 0 else int(top_region_height * 1.6)
    total_h = int(top_area_height + (rows * strip_h) + ((rows - 1) * bag_gap) + bottom_margin)

    render_style = config.get("render_style", "lightbox")
    colors = STYLE_COLORS.get(render_style, STYLE_COLORS["lightbox"])

    big_total_w = total_w * aa_scale
    big_total_h = total_h * aa_scale
    big_canvas = Image.new("RGB", (big_total_w, big_total_h), colors["canvas_bg"])
    big_draw = ImageDraw.Draw(big_canvas)

    big_current_y = top_area_height * aa_scale
    big_side_margin = side_margin * aa_scale
    big_spacing = spacing * aa_scale
    big_thumb_w = thumb_w * aa_scale
    big_img_h = img_h * aa_scale
    big_strip_h = strip_h * aa_scale
    big_bag_gap = bag_gap * aa_scale
    big_image_border = image_border * aa_scale

    edge_font_sz = int(16 * base_scale) * aa_scale
    info_font_sz = int(info_font_size * aa_scale)

    # Pack image placement
    big_text_area_left = big_side_margin
    big_text_area_right = big_total_w - big_side_margin
    if pack_img and info_height > 0:
        orig_w, orig_h = pack_img.size
        top_blank_height = top_margin + info_height + info_to_film_gap
        pack_h_display = min(int(top_blank_height * pack_size_pct / 100.0), 100)
        pack_w_display = int(pack_h_display * (orig_w / orig_h))
        max_allow_w = int(total_w * 0.35)
        if pack_w_display > max_allow_w:
            pack_w_display = max_allow_w
            pack_h_display = int(pack_w_display * (orig_h / orig_w))
        if pack_w_display > 0 and pack_h_display > 0:
            big_pack = pack_img.resize((pack_w_display * aa_scale, pack_h_display * aa_scale), Image.Resampling.LANCZOS)
            pack_y = (top_blank_height - pack_h_display) // 2 * aa_scale
            if pack_position == "left":
                pack_x = big_side_margin
                if has_pack_stroke:
                    pb = pack_border * aa_scale
                    big_draw.rectangle([pack_x - pb, pack_y - pb, pack_x + pack_w_display * aa_scale + pb, pack_y + pack_h_display * aa_scale + pb],
                                       outline=colors["pack_border"], width=pb)
                big_canvas.paste(big_pack, (pack_x, pack_y))
                big_text_area_left = pack_x + pack_w_display * aa_scale + pack_gap * aa_scale
            else:
                pack_x = big_total_w - big_side_margin - pack_w_display * aa_scale
                if has_pack_stroke:
                    pb = pack_border * aa_scale
                    big_draw.rectangle([pack_x - pb, pack_y - pb, pack_x + pack_w_display * aa_scale + pb, pack_y + pack_h_display * aa_scale + pb],
                                       outline=colors["pack_border"], width=pb)
                big_canvas.paste(big_pack, (pack_x, pack_y))
                big_text_area_right = pack_x - pack_gap * aa_scale

    # Info block
    if has_info:
        font_main = _load_font_cached(info_font_sz)
        if font_main:
            _draw_info_block_api(
                big_draw, font_main, colors,
                big_text_area_left, big_text_area_right,
                top_margin * aa_scale, info_top_padding * aa_scale,
                info_line_height * aa_scale, thumb_w, config
            )

    # Edge text info
    edge_info = generate_edge_text(config.get("info_film", ""), config.get("edge_text", ""))

    film_base = colors["film_base"]
    border_mid = int(0.9 * scale_factor * aa_scale)

    img_idx = 0
    for row in range(rows):
        y1 = int(big_current_y)
        y2 = int(big_current_y + big_strip_h)
        big_draw.rectangle([0, y1, big_total_w, y2], fill=film_base)

        # Edge text — centered in the 2.5mm border area (no perforations for 120)
        font = _load_font_cached(edge_font_sz)
        if font:
            color = colors["text_color"]
            top_parts = [edge_info["brand"]]
            if edge_info["film_type"]:
                top_parts.append(edge_info["film_type"])
            top_line = "  ".join(top_parts)
            big_draw.text((big_total_w // 2, y1 + border_mid), top_line, fill=color, font=font, anchor="mm")

            # Bottom edge: numbers + triangles at image centers
            edge_y_bottom = y2 - border_mid
            start_col = 2 if row == 0 else 0
            image_centers = []
            for c in range(start_col, cols):
                if img_idx >= len(images):
                    break
                cx = big_side_margin + big_spacing + c * (big_thumb_w + big_spacing) + big_thumb_w // 2
                image_centers.append((cx, img_idx + 1))
                img_idx += 1

            for cx, num in image_centers:
                num_str = str(num)
                bbox = big_draw.textbbox((0, 0), num_str, font=font)
                num_w = bbox[2] - bbox[0]
                big_draw.text((cx - num_w // 2, edge_y_bottom), num_str, fill=color, font=font, anchor="mm")
                gap = 25 * aa_scale
                _draw_triangle_api(big_draw, cx - num_w // 2 + num_w + gap, edge_y_bottom, edge_font_sz * 0.7, color)

            # Separator triangles between adjacent images
            for i in range(len(image_centers) - 1):
                sep_x = (image_centers[i][0] + image_centers[i + 1][0]) // 2
                _draw_triangle_api(big_draw, sep_x, edge_y_bottom, edge_font_sz * 0.6, color)
        else:
            # Advance img_idx even without font
            start_col = 2 if row == 0 else 0
            for c in range(start_col, cols):
                if img_idx >= len(images):
                    break
                img_idx += 1

        # Place images in this row
        start_col = 2 if row == 0 else 0
        row_img_idx = img_idx - cols + start_col  # recalculate from row start
        for col in range(start_col, cols):
            if row_img_idx >= len(images):
                break
            x_pos = big_side_margin + big_spacing + col * (big_thumb_w + big_spacing)
            y_img_top = y1 + big_image_border
            placed = cover_resize_crop(images[row_img_idx], big_thumb_w, big_img_h)
            big_canvas.paste(placed, (int(x_pos), int(y_img_top)))
            row_img_idx += 1

        big_current_y += big_strip_h + big_bag_gap

    # Downscale if AA
    if not is_preview:
        canvas = big_canvas.resize((total_w, total_h), Image.Resampling.LANCZOS)
    else:
        canvas = big_canvas

    # Watermark
    sig = config.get("signature", "").strip()
    if sig:
        wm_font = _load_font_cached(int(18 * base_scale))
        if wm_font:
            margin = int(30 * base_scale)
            bbox = big_draw.textbbox((0, 0), sig, font=wm_font)
            tw = bbox[2] - bbox[0]
            th = bbox[3] - bbox[1]
            wx = total_w - margin - tw
            wy = total_h - margin - th
            big_draw.text((wx + 1, wy + 1), sig, fill=(0, 0, 0), font=wm_font, anchor="lt")
            big_draw.text((wx, wy), sig, fill=colors.get("text_color", (255, 255, 255)), font=wm_font, anchor="lt")

    return canvas


# ---------------------------------------------------------------------------
# API Endpoint
# ---------------------------------------------------------------------------

@app.post("/render", summary="渲染胶片排版图", description="上传图片和配置参数，返回渲染好的胶片排版图片（JPEG/PNG）。")
async def render_film_sheet(
    images: list[UploadFile] = Form(..., description="胶片扫描图片（支持 JPG/PNG/TIFF/BMP，可多选）"),
    film_format: str = Form("135", description="画幅：135 或 120"),
    sub_format: str = Form("标准 36×24", description="子画幅：135 支持 标准/半格/方形/XPan；120 支持 645/66/67/68/69/612/617"),
    thumb_width: int = Form(400, ge=300, description="缩略图宽度（最小300px，越大越清晰）"),
    columns: int = Form(6, ge=3, le=10, description="每行列数"),
    spacing: int = Form(20, description="图片间距"),
    force_landscape: bool = Form(True, description="强制横向（竖图自动旋转90°）"),
    processing_mode: str = Form("positive", description="成像模式：positive 正片 / negative 负片"),
    render_style: str = Form("lightbox", description="渲染风格：lightbox 灯板正片 / contact_sheet 接触印相"),
    output_format: str = Form("JPG", description="输出格式：JPG 或 PNG"),
    quality: int = Form(95, ge=1, le=100, description="JPG 质量（1-100，仅 JPG 有效）"),
    info_roll: str = Form("", description="卷号（用于自动命名和边字）"),
    info_camera: str = Form("", description="相机型号"),
    info_film: str = Form("", description="胶卷名称（用于边字自动识别品牌）"),
    info_shoot_date: str = Form("", description="拍摄日期"),
    info_dev_date: str = Form("", description="冲洗日期"),
    info_proc: str = Form("", description="冲洗方式"),
    info_lab: str = Form("", description="冲洗地点"),
    info_scanner: str = Form("", description="扫描仪型号"),
    info_lang: str = Form("en", description="标签语言：zh 中文 / en 英文"),
    edge_text: str = Form("", description="自定义边字（留空则自动从胶卷信息生成）"),
    pack_image_path: str = Form("", description="胶卷包装图片路径（可选）"),
    pack_position: str = Form("left", description="包装图位置：left 左侧 / right 右侧"),
    pack_border_stroke: bool = Form(True, description="包装图描边"),
    pack_size: int = Form(80, ge=10, le=100, description="包装图大小百分比"),
    perf_mode: str = Form("Auto", description="齿孔模式：Auto 自动 / KS 民用 / BH 电影"),
    signature: str = Form("", description="水印签名（右下角显示）"),
    is_preview: bool = Form(False, description="预览模式（关闭抗锯齿，加快渲染）"),
    batch_export_enabled: bool = Form(False, description="批量导出（同时生成另一种风格）"),
    pack_image_file: Optional[UploadFile] = Form(None, description="胶卷包装图片（可选）"),
):
    """Render a film sheet from uploaded images + config.

    Returns a JPEG/PNG image directly (no file I/O).
    """
    # Validate config
    config = {
        "film_format": film_format,
        "sub_format": sub_format,
        "thumb_width": thumb_width,
        "columns": columns,
        "spacing": spacing,
        "force_landscape": force_landscape,
        "processing_mode": processing_mode,
        "render_style": render_style,
        "output_format": output_format,
        "quality": quality,
        "info_roll": info_roll,
        "info_camera": info_camera,
        "info_film": info_film,
        "info_shoot_date": info_shoot_date,
        "info_dev_date": info_dev_date,
        "info_proc": info_proc,
        "info_lab": info_lab,
        "info_scanner": info_scanner,
        "info_lang": info_lang,
        "edge_text": edge_text,
        "pack_image": pack_image_path,
        "pack_position": pack_position,
        "pack_border_stroke": pack_border_stroke,
        "pack_size": pack_size,
        "perf_mode": perf_mode,
        "signature": signature,
        "batch_export_enabled": batch_export_enabled,
    }

    is_valid, errors = validate_config(config)
    if not is_valid:
        return Response(content=f"配置错误: {'; '.join(errors)}", media_type="text/plain", status_code=400)

    # Load images from uploaded files
    pil_images = []
    for f in images:
        data = await f.read()
        try:
            img = Image.open(io.BytesIO(data)).convert("RGB")
            pil_images.append(img)
        except Exception:
            pass

    if not pil_images:
        return Response(content="没有可处理的图片", media_type="text/plain", status_code=400)

    # Save pack image to temp file if provided
    pack_img_path = pack_image_path  # from form field (filesystem path from desktop)
    if pack_image_file and pack_image_file.filename:
        tmp_dir = tempfile.mkdtemp(prefix="filmsheet_")
        pack_img_path = os.path.join(tmp_dir, pack_image_file.filename)
        with open(pack_img_path, "wb") as f:
            f.write(await pack_image_file.read())
        config["pack_image"] = pack_img_path

    # Route to renderer
    if film_format == "120":
        canvas = _render_120_api(pil_images, config, is_preview=is_preview)
    else:
        canvas = _render_135_api(pil_images, config, is_preview=is_preview)

    # Return as image
    buf = io.BytesIO()
    if output_format.upper() == "PNG":
        canvas.save(buf, format="PNG", compress_level=1)
    else:
        canvas.save(buf, format="JPEG", quality=quality, optimize=True)

    media_type = "image/png" if output_format.upper() == "PNG" else "image/jpeg"
    return Response(content=buf.getvalue(), media_type=media_type)


@app.get("/health")
async def health():
    return {"status": "ok", "service": "FilmSheet API", "version": "1.5.0"}
