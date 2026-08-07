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
import time
from collections import defaultdict, deque
from typing import Optional

# Resolve import of _version whether running from api/ or as installed package.
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
from filmsheet._version import __VERSION__

from fastapi import FastAPI, Header, HTTPException, Request, UploadFile, Form
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import Response
from PIL import Image, ImageOps

from processor.config_schema import validate_config
from processor.image_pipeline import process_135_image as _process_135_image
from processor.image_pipeline import process_120_image as _process_120_image
from processor.api_render import render_135, render_120

MAX_UPLOAD_IMAGES = 72
MAX_UPLOAD_BYTES_PER_FILE = 40 * 1024 * 1024
MAX_UPLOAD_BYTES_TOTAL = 200 * 1024 * 1024
MAX_IMAGE_PIXELS = 80_000_000
ALLOWED_IMAGE_FORMATS = {"JPEG", "PNG", "TIFF", "BMP"}
RATE_LIMIT_REQUESTS = int(os.getenv("FILMSHEET_RATE_LIMIT_REQUESTS", "20"))
RATE_LIMIT_WINDOW_SECONDS = int(os.getenv("FILMSHEET_RATE_LIMIT_WINDOW_SECONDS", "60"))
API_KEY = os.getenv("FILMSHEET_API_KEY")
_request_times: dict[str, deque[float]] = defaultdict(deque)


def _enforce_request_limits(request: Request, api_key: Optional[str]) -> None:
    """Apply optional API-key auth and a small per-process client rate limit."""
    if API_KEY and api_key != API_KEY:
        raise HTTPException(status_code=401, detail="Invalid API key")

    client = request.client.host if request.client else "unknown"
    now = time.monotonic()
    timestamps = _request_times[client]
    while timestamps and now - timestamps[0] >= RATE_LIMIT_WINDOW_SECONDS:
        timestamps.popleft()
    if len(timestamps) >= RATE_LIMIT_REQUESTS:
        raise HTTPException(status_code=429, detail="Render rate limit exceeded; try again shortly")
    timestamps.append(now)


async def _read_upload(upload: UploadFile, remaining_bytes: int) -> bytes:
    """Read and validate one image upload without accepting unlimited input."""
    max_bytes = min(MAX_UPLOAD_BYTES_PER_FILE, remaining_bytes)
    data = await upload.read(max_bytes + 1)
    if len(data) > max_bytes:
        raise HTTPException(status_code=413, detail=f"{upload.filename or 'Image'} is too large")
    if not data:
        raise HTTPException(status_code=400, detail=f"{upload.filename or 'Image'} is empty")

    try:
        with Image.open(io.BytesIO(data)) as image:
            if image.format not in ALLOWED_IMAGE_FORMATS:
                raise HTTPException(status_code=415, detail="Only JPEG, PNG, TIFF, and BMP images are supported")
            if image.width * image.height > MAX_IMAGE_PIXELS:
                raise HTTPException(status_code=413, detail="Image dimensions exceed the 80-megapixel limit")
    except HTTPException:
        raise
    except (Image.UnidentifiedImageError, Image.DecompressionBombError, OSError) as exc:
        raise HTTPException(status_code=400, detail=f"Invalid image upload: {upload.filename or 'unnamed'}") from exc
    return data

# ---------------------------------------------------------------------------
# App
# ---------------------------------------------------------------------------

app = FastAPI(
    title="FilmSheet API",
    description="胶片扫描排版渲染服务 — 把你的数码扫描件变成真实的灯箱灯板 / 接触印相作品。",
    version=__VERSION__,
)

allowed_origins = [origin.strip() for origin in os.getenv("FILMSHEET_CORS_ORIGINS", "").split(",") if origin.strip()]
if allowed_origins:
    app.add_middleware(
        CORSMiddleware,
        allow_origins=allowed_origins,
        allow_methods=["GET", "POST"],
        allow_headers=["Content-Type", "X-API-Key"],
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
    request: Request,
    images: list[UploadFile] = Form(..., description="胶片扫描图片（支持 JPG/PNG/TIFF/BMP，可多选）"),
    film_format: str = Form("135", description="画幅：135 或 120"),
    sub_format: str = Form("标准 36×24", description="子画幅：135 支持 标准/半格/方形/XPan；120 支持 645/66/67/68/69/612/617"),
    thumb_width: int = Form(400, ge=300, le=1200, description="缩略图宽度（300-1200px）"),
    columns: int = Form(6, ge=1, le=20, description="每行列数"),
    spacing: int = Form(20, ge=0, le=200, description="图片间距"),
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
    pack_image_path: str = Form("", description="已弃用：请使用 pack_image_file 上传包装图"),
    pack_position: str = Form("left", description="包装图位置：left 左侧 / right 右侧"),
    pack_border_stroke: bool = Form(True, description="包装图描边"),
    pack_size: int = Form(80, ge=10, le=100, description="包装图大小百分比"),
    perf_mode: str = Form("Auto", description="齿孔模式：Auto 自动 / KS 民用 / BH 电影"),
    signature: str = Form("", description="水印签名（右下角显示）"),
    is_preview: bool = Form(False, description="预览模式（关闭抗锯齿，加快渲染）"),
    batch_export_enabled: bool = Form(False, description="批量导出（同时生成另一种风格）"),
    single_photo: bool = Form(False, description="单张模式（胶卷条排版）"),
    pack_image_file: Optional[UploadFile] = Form(None, description="胶卷包装图片（可选）"),
    x_api_key: Optional[str] = Header(None),
):
    """Render a film sheet from uploaded images + config.

    Returns a JPEG/PNG image directly (no file I/O).
    """
    _enforce_request_limits(request, x_api_key)
    if len(images) > MAX_UPLOAD_IMAGES:
        raise HTTPException(status_code=413, detail=f"A render accepts at most {MAX_UPLOAD_IMAGES} images")
    if pack_image_path:
        raise HTTPException(status_code=400, detail="Upload pack_image_file instead of supplying a server file path")

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
        "pack_image": "",
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

    # Normalize uploads into a request-scoped temporary directory. It is removed
    # even if preprocessing or rendering raises an exception.
    total_bytes = 0
    with tempfile.TemporaryDirectory(prefix="filmsheet_") as tmp_dir:
        pil_images = []
        for index, upload in enumerate(images):
            data = await _read_upload(upload, MAX_UPLOAD_BYTES_TOTAL - total_bytes)
            total_bytes += len(data)
            with Image.open(io.BytesIO(data)) as source:
                image = ImageOps.exif_transpose(source).convert("RGB")

            tmp_path = os.path.join(tmp_dir, f"input-{index}.png")
            image.save(tmp_path)
            if film_format == "120":
                processed = _process_120_image(
                    tmp_path, sub_format or "66", thumb_width,
                    processing_mode, force_landscape,
                )
            else:
                processed = _process_135_image(
                    tmp_path, thumb_width, processing_mode, force_landscape, sub_format,
                )
            if processed is None:
                raise HTTPException(status_code=422, detail=f"Unable to process {upload.filename or f'image {index + 1}'}")
            pil_images.append(processed)

        if pack_image_file and pack_image_file.filename:
            pack_data = await _read_upload(pack_image_file, MAX_UPLOAD_BYTES_TOTAL - total_bytes)
            with Image.open(io.BytesIO(pack_data)) as source:
                pack_image = ImageOps.exif_transpose(source).convert("RGB")
            pack_path = os.path.join(tmp_dir, "pack.png")
            pack_image.save(pack_path)
            config["pack_image"] = pack_path

        try:
            canvas = render_120(pil_images, config, is_preview=is_preview) if film_format == "120" \
                else render_135(pil_images, config, is_preview=is_preview)
        except Exception as exc:
            raise HTTPException(status_code=422, detail="Unable to render this film sheet") from exc

        buf = io.BytesIO()
        if output_format.upper() == "PNG":
            canvas.save(buf, format="PNG", compress_level=1)
        else:
            canvas.save(buf, format="JPEG", quality=quality, optimize=True)
        output = buf.getvalue()

    media_type = "image/png" if output_format.upper() == "PNG" else "image/jpeg"
    return Response(content=output, media_type=media_type)


@app.get("/health")
async def health():
    return {"status": "ok", "service": "FilmSheet API", "version": __VERSION__}
