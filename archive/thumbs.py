# -*- coding: utf-8 -*-
"""缩略图生成与磁盘缓存。

底片册网格要显示每卷的样片，但产物可能是 6000px宽的大图，直接塞进 Tk 会
把内存吃光并卡住界面。这里的策略：

- 缩略图统一按**最长边**缩放（不是固定宽度），网格单元才是固定宽高比
- 缓存到 ``<归档目录>/thumbs/<roll_id>_<style>.jpg``，只生成一次
- Pillow 的导入和resize 都在函数内部 —— 这个包不能因为缩略图就拖慢启动
- 磁盘缓存命中时直接返回路径，不碰 Pillow
"""

from __future__ import annotations

import os

# 网格单元里样片的显示尺寸（像素），实际缓存图按2 倍存，留给高 DPI 缩放
THUMB_BOX = (320, 320)
_CACHE_QUALITY = 82


def thumbs_dir() -> str:
    """缩略图缓存目录：跟随底片册位置，挪库时一起挪。"""
    from .store import archive_dir
    return os.path.join(archive_dir(), "thumbs")


def thumb_path(roll_id: str, style: str = "main") -> str:
    safe = "".join(c for c in (roll_id or "x") if c.isalnum() or c in "_-")
    return os.path.join(thumbs_dir(), f"{safe}_{style}.jpg")


def _fit_box(img_w: int, img_h: int, box: tuple[int, int]) -> tuple[int, int]:
    """等比缩到 box 内，不放大原图（放大小图只会浪费空间和显存）。"""
    bw, bh = box
    if img_w <= 0 or img_h <= 0:
        return bw, bh
    scale = min(bw / img_w, bh / img_h, 1.0)
    return max(1, int(img_w * scale)), max(1, int(img_h * scale))


def make_thumb(src_path: str, dst_path: str,
               box: tuple[int, int] = THUMB_BOX) -> str | None:
    """生成缩略图并缓存。成功返回 dst_path，失败返回 None（不抛异常）。

    缩略图是锦上添花，源图坏了不该让底片册整页打不开——所以全程吞异常。
    """
    if not src_path or not os.path.isfile(src_path):
        return None
    if os.path.isfile(dst_path) and os.path.getsize(dst_path) > 0:
        return dst_path
    try:
        from PIL import Image, ImageOps
    except ImportError:
        return None

    try:
        os.makedirs(os.path.dirname(dst_path), exist_ok=True)
        with Image.open(src_path) as im:
            im = ImageOps.exif_transpose(im)      # 竖拍的照片靠这个才立得住
            im = im.convert("RGB")
            im.thumbnail(box, Image.LANCZOS)
            im.save(dst_path, "JPEG", quality=_CACHE_QUALITY,
                    optimize=True, progressive=True)
        return dst_path
    except Exception:
        # 半截文件留着会让下次误命中缓存
        try:
            if os.path.exists(dst_path):
                os.unlink(dst_path)
        except OSError:
            pass
        return None


def thumb_for_roll(roll, box: tuple[int, int] = THUMB_BOX) -> str | None:
    """取某卷的代表缩略图，优先用已有的缓存。"""
    cached = roll.thumbs.get("main")
    if cached and os.path.isfile(cached):
        return cached

    out = roll.primary_output
    if out is None or not out.exists():
        return None

    dst = thumb_path(roll.id, "main")
    made = make_thumb(out.path, dst, box)
    if made:
        roll.thumbs["main"] = made
    return made


def clear_thumb(roll_id: str):
    """卷宗删除后清掉它的缓存，避免 thumbs 目录无限膨胀。"""
    try:
        d = thumbs_dir()
        if not os.path.isdir(d):
            return
        for f in os.listdir(d):
            if f.startswith(roll_id):
                try:
                    os.unlink(os.path.join(d, f))
                except OSError:
                    pass
    except OSError:
        pass