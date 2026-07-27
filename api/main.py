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
import tempfile
from typing import Optional

# Resolve import of _version whether running from api/ or as installed package.
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
from filmsheet._version import __VERSION__

from fastapi import FastAPI, UploadFile, Form
from fastapi.responses import Response
from PIL import Image, ImageDraw, ImageFont

from processor.config_schema import validate_config, sanitize_config, FIELD_DEFS
from processor.edge_text import generate_edge_text
from processor.image_pipeline import process_135_image as _process_135_image
from processor.image_pipeline import process_120_image as _process_120_image
from processor.api_render import render_135, render_120
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
    version=__VERSION__,
)

# Serve mobile web frontend
@app.get("/")
async def serve_web():
    html_path = os.path.join(os.path.dirname(__file__), "index.html")
    with open(html_path, "r", encoding="utf-8") as f:
        html = f.read()
    html = html.replace("{VERSION}", __VERSION__)
    return Response(content=html, media_type="text/html; charset=utf-8")


# ---------------------------------------------------------------------------
# API Endpoint
# ---------------------------------------------------------------------------

@app.post("/render", summary="渲染胶片排版图", description="上传图片和配置参数，返回渲染好的胶片排版图片（JPEG/PNG）。")
async def render_film_sheet(
    images: list[UploadFile] = Form(..., description="胶片扫描图片（支持 JPG/PNG/TIFF/BMP，可多选）"),
    film_format: str = Form("135", description="画幅：135 或 120"),
    sub_format: str = Form("标准 36×24", description="子画幅：135 支持 标准/半格/方形/XPan；120 支持 645/66/67/68/69/612/617"),
    thumb_width: int = Form(400, ge=300, description="缩略图宽度（最小300px，越大越清晰）"),
    columns: int = Form(6, ge=1, le=99, description="每行列数"),
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
    single_photo: bool = Form(False, description="单张模式（胶卷条排版）"),
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
        "single_photo_mode": single_photo,
    }

    is_valid, errors = validate_config(config)
    if not is_valid:
        return Response(content=f"配置错误: {'; '.join(errors)}", media_type="text/plain", status_code=400)

    # Load images from uploaded files — process through the same pipeline as desktop
    tmp_dir = tempfile.mkdtemp(prefix="filmsheet_")
    try:
        for idx, f in enumerate(images):
            data = await f.read()
            try:
                img = Image.open(io.BytesIO(data)).convert("RGB")
            except Exception:
                continue

            if film_format == "120":
                sub_fmt = sub_format or "66"
                tmp_path = os.path.join(tmp_dir, f"{idx}.png")
                img.save(tmp_path)
                processed = _process_120_image(
                    tmp_path, sub_fmt, thumb_width,
                    processing_mode, force_landscape
                )
            else:
                tmp_path = os.path.join(tmp_dir, f"{idx}.png")
                img.save(tmp_path)
                processed = _process_135_image(
                    tmp_path, thumb_width, processing_mode, force_landscape, sub_format
                )

            if processed is not None:
                pil_images.append(processed)
    finally:
        import shutil
        shutil.rmtree(tmp_dir, ignore_errors=True)

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

    # Route to renderer (uses shared Desktop rendering pipeline)
    if film_format == "120":
        canvas = render_120(pil_images, config, is_preview=is_preview)
    else:
        canvas = render_135(pil_images, config, is_preview=is_preview)

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
    return {"status": "ok", "service": "FilmSheet API", "version": __VERSION__}
