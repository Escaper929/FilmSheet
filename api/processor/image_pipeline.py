# -*- coding: utf-8 -*-
"""Image preprocessing pipeline.

Pure functions for processing individual image files into thumbnails.
Independent of rendering, config, or UI — suitable for Web/mobile API.
"""

from PIL import Image, ImageOps
from typing import Optional


def process_135_image(
    filepath: str,
    thumb_width: int = 400,
    processing_mode: str = "positive",
    force_landscape: bool = True,
) -> Optional[Image.Image]:
    """Process a single 135 image file.

    Steps: open → convert → invert if negative → rotate if landscape → crop → resize.

    Args:
        filepath: Path to the image file.
        thumb_width: Target thumbnail width in pixels.
        processing_mode: "positive" or "negative".
        force_landscape: Rotate portrait images to landscape.

    Returns:
        Processed PIL Image, or None on failure.
    """
    try:
        img = Image.open(filepath)
        if img.mode != 'RGB':
            img = img.convert('RGB')
        if processing_mode == 'negative':
            img = ImageOps.invert(img)
        w, h = img.size
        if force_landscape and h > w:
            img = img.rotate(-90, expand=True)
        img = _crop_to_135_ratio(img)
        target_h = int(thumb_width * 24.0 / 36.0)
        img = img.resize((thumb_width, target_h), Image.Resampling.LANCZOS)
        return img
    except Exception:
        return None


def process_120_image(
    filepath: str,
    target_ratio: float,
    thumb_width: int = 400,
    processing_mode: str = "positive",
    force_landscape: bool = True,
) -> Optional[Image.Image]:
    """Process a single 120 image file.

    Args:
        filepath: Path to the image file.
        target_ratio: Aspect ratio (e.g. 1.5 for 645, 1.0 for 66).
        thumb_width: Target thumbnail width in pixels.
        processing_mode: "positive" or "negative".
        force_landscape: Rotate portrait images to landscape.

    Returns:
        Processed PIL Image, or None on failure.
    """
    try:
        img = Image.open(filepath)
        if img.mode != 'RGB':
            img = img.convert('RGB')
        if processing_mode == 'negative':
            img = ImageOps.invert(img)
        w, h = img.size
        if force_landscape and h > w:
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


def _crop_to_135_ratio(img: Image.Image) -> Image.Image:
    """Crop image to 135 standard 36:24 ratio, centered."""
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


def cover_resize_crop(img: Image.Image, target_w: int, target_h: int) -> Image.Image:
    """Cover-resize and center-crop an image to target dimensions."""
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
    return img.crop((left, top, left + target_w, top + target_h))
