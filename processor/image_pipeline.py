# -*- coding: utf-8 -*-
"""Image preprocessing pipeline.

Pure functions for processing individual image files into thumbnails.
Independent of rendering, config, or UI — suitable for Web/mobile API.
"""

from PIL import Image, ImageOps
from typing import Optional


from utils.helpers import FILM_FORMAT_RATIOS


def process_135_image(
    filepath: str,
    thumb_width: int = 400,
    processing_mode: str = "positive",
    force_landscape: bool = True,
    sub_format: str = "标准 36×24",
) -> Optional[Image.Image]:
    """Process a single 135 image file.

    Steps: open → convert → invert if negative → rotate to match sub-format orientation
    → crop to frame ratio → resize to thumbnail width.

    The rotation rule depends on the sub-format's expected aspect ratio:
      - standard 36×24 (ratio 1.5):  long edge down → landscape
      - semi-frame 18×24 (ratio 0.75): short edge down → portrait
      - square 24×24 (ratio 1.0): no rotation needed
      - XPan 65×24 (ratio ~2.7): long edge down → landscape

    Args:
        filepath: Path to the image file.
        thumb_width: Target thumbnail width in pixels.
        processing_mode: "positive" or "negative".
        force_landscape: Legacy flag — still respected but overridden by sub_format.
        sub_format: 135 sub-format determining expected orientation and crop ratio.

    Returns:
        Processed PIL Image, or None on failure.
    """
    try:
        img = Image.open(filepath)
        if img.mode != 'RGB':
            img = img.convert('RGB')
        if processing_mode == 'negative':
            img = ImageOps.invert(img)

        # Determine expected ratio from sub-format name and apply rotation
        expected_ratio = _expected_135_ratio(sub_format)
        img = _maybe_rotate_135(img, expected_ratio, force_landscape)

        img = _crop_to_135_ratio_custom(img, expected_ratio)
        target_h = int(thumb_width / expected_ratio)
        img = img.resize((thumb_width, target_h), Image.LANCZOS)
        return img
    except Exception:
        return None


# ---------------------------------------------------------------------------
# 135 sub-format expectation helpers
# ---------------------------------------------------------------------------

def _expected_135_ratio(sub_format: str) -> float:
    """Return W/H ratio for a 135 sub-format.

    Ratio < 1 means short-edge-down (portrait expected), > 1 means
    long-edge-down (landscape expected).
    """
    mapping = {
        "标准 36×24": 36.0 / 24.0,   # 1.5 — landscape
        "半格 18×24": 18.0 / 24.0,   # 0.75 — portrait (short edge down)
        "方形 24×24": 24.0 / 24.0,   # 1.0 — square
        "XPan 65×24": 65.0 / 24.0,   # ~2.71 — ultra-wide landscape
    }
    return mapping.get(sub_format, 36.0 / 24.0)  # default to standard


def _maybe_rotate_135(img: Image.Image, expected_ratio: float, force_landscape: bool) -> Image.Image:
    """Rotate image so its orientation matches *expected_ratio*.

    - expected_ratio > 1 (landscape): vertical images become horizontal.
    - expected_ratio < 1 (portrait): horizontal images become vertical.
    - expected_ratio == 1 (square): no rotation.
    Returns the (possibly rotated) image.
    """
    if not force_landscape or expected_ratio == 1.0:
        return img
    w, h = img.size
    if expected_ratio > 1 and h > w:
        # Expecting landscape but image is portrait → rotate -90°
        return img.rotate(-90, expand=True)
    elif expected_ratio < 1 and w > h:
        # Expecting portrait but image is landscape → rotate 90°
        return img.rotate(90, expand=True)
    return img


def _crop_to_135_ratio_custom(img: Image.Image, target_ratio: float) -> Image.Image:
    """Crop image to *target_ratio* (W/H), centered."""
    w, h = img.size
    if w / h > target_ratio:
        new_w = int(h * target_ratio)
        left = (w - new_w) // 2
        return img.crop((left, 0, left + new_w, h))
    else:
        new_h = int(w / target_ratio)
        top = (h - new_h) // 2
        return img.crop((0, top, w, top + new_h))


# ---------------------------------------------------------------------------
# 120 helpers
# ---------------------------------------------------------------------------

def _crop_to_target_ratio(img: Image.Image, target_ratio: float) -> Image.Image:
    """Center-crop image to target W/H ratio."""
    w, h = img.size
    if w / h > target_ratio:
        new_w = int(h * target_ratio)
        left = (w - new_w) // 2
        return img.crop((left, 0, left + new_w, h))
    else:
        new_h = int(w / target_ratio)
        top = (h - new_h) // 2
        return img.crop((0, top, w, top + new_h))


def _is_portrait_120_format(sub_format: str) -> bool:
    """Return True if the sub-format expects a portrait photo (taller than wide).

    Only 645 is shot with the long edge along the film width — all other formats
    have the long edge along the film advance direction, so the photo is landscape.
    """
    return sub_format == "645"


def _maybe_rotate_landscape_to_portrait(img: Image.Image, force_landscape: bool) -> Image.Image:
    """For portrait formats (645): rotate landscape input to portrait."""
    if not force_landscape:
        return img
    w, h = img.size
    if w > h:
        return img.rotate(-90, expand=True)
    return img


def _maybe_rotate_portrait_to_landscape(img: Image.Image, force_landscape: bool) -> Image.Image:
    """For landscape formats (all except 645 and square): rotate portrait to landscape."""
    if not force_landscape:
        return img
    w, h = img.size
    if h > w:
        return img.rotate(-90, expand=True)
    return img


def process_120_image(
    filepath: str,
    sub_format: str = "66",
    thumb_width: int = 400,
    processing_mode: str = "positive",
    force_landscape: bool = True,
) -> Optional[Image.Image]:
    """Process a single 120 image file.

    Args:
        filepath: Path to the image file.
        sub_format: 120 sub-format determining expected orientation.
            - 66: square → no rotation needed
            - 645: portrait photo (base runs vertically) → rotate landscape→portrait
            - 其余格式 (67/68/69/612/617): landscape photo → rotate portrait→landscape
        thumb_width: Target thumbnail width in pixels.
        processing_mode: "positive" or "negative".
        force_landscape: Whether to enforce landscape/portrait orientation matching sub_format.

    Returns:
        Processed PIL Image, or None on failure.
    """
    try:
        img = Image.open(filepath)
        if img.mode != 'RGB':
            img = img.convert('RGB')
        if processing_mode == 'negative':
            img = ImageOps.invert(img)

        # Determine expected ratio and whether portrait format needs special handling
        expected_ratio = FILM_FORMAT_RATIOS.get(sub_format, 1.0)
        # is_portrait_format: true for 645 where user's photo is shot in portrait
        is_portrait_format = _is_portrait_120_format(sub_format)

        if is_portrait_format:
            # 645: user photographs in portrait. If landscape input, rotate to portrait.
            img = _maybe_rotate_landscape_to_portrait(img, force_landscape)
        else:
            # 66/67/68/69/612/617: user photographs in landscape. If portrait input, rotate to landscape.
            img = _maybe_rotate_portrait_to_landscape(img, force_landscape)

        # Center-crop to exact target aspect ratio
        img = _crop_to_target_ratio(img, expected_ratio)

        target_h = int(thumb_width / expected_ratio)
        img = img.resize((thumb_width, target_h), Image.LANCZOS)
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
    """Cover-resize and center-crop an image to target dimensions.

    Uses a two-pass strategy: fast BICUBIC resize to approximate scale,
    then LANCZOS for final size. This avoids one expensive LANCZOS operation
    while keeping visual quality acceptable (the thumbnail stage already reduced
    resolution).
    """
    img_w, img_h = img.size
    if img_w == 0 or img_h == 0:
        return img

    scale = max(target_w / img_w, target_h / img_h)
    new_w = int(round(img_w * scale))
    new_h = int(round(img_h * scale))

    # Case A: no scaling needed → just center-crop
    if new_w == img_w and new_h == img_h:
        result = img
    # Case B: single LANCZOS pass suffices (intermediate == target)
    elif new_w == target_w and new_h == target_h:
        result = img.resize((target_w, target_h), Image.LANCZOS)
    # Case C: two-pass — BICUBIC first, then LANCZOS final
    else:
        intermediate = img.resize((new_w, new_h), Image.BICUBIC)
        result = intermediate.resize((target_w, target_h), Image.LANCZOS)

    left = (result.width - target_w) // 2
    top = (result.height - target_h) // 2
    return result.crop((left, top, left + target_w, top + target_h))
