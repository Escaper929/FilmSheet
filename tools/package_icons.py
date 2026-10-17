#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""图标打包脚本 —— 只做格式转换与落位，不做任何设计。

把既有的主图套件（默认桌面 filmsheet_icons/）原样搬进仓库：

  * 8 件套图（icon-1024 / icon-macos-1024 / icon-512 / icon-192 /
    icon-64 / apple-touch-icon / favicon-32x32 / icon.ico）
    原样复制到仓库 assets/
  * 用 icon-macos-1024.png 生成 assets/icon.icns（同一张图的容器转换，
    供 main.spec 的 BUNDLE 与 setup.py 的 py2app 使用）
  * icon-192.png / icon-512.png 复制到 api/（PWA / manifest 用）

要改设计请改主图本身，然后重跑本脚本：

  python tools/package_icons.py [--src DIR]
"""

import argparse
import io
import os
import struct
import sys

from PIL import Image

SET_FILES = [
    "icon-1024.png",
    "icon-macos-1024.png",
    "icon-512.png",
    "icon-192.png",
    "icon-64.png",
    "apple-touch-icon.png",
    "favicon-32x32.png",
    "icon.ico",
]

# .icns 条目（类型, 像素尺寸）—— 与 iconutil 生成的布局一致
ICNS_ENTRIES = [
    ("ic07", 128), ("ic08", 256), ("ic09", 512), ("ic10", 1024),
    ("ic11", 64), ("ic12", 128), ("ic13", 512), ("ic14", 1024),
]


def build_icns(master_path, out_path):
    """由一张 PNG 主图组装 .icns（PNG 条目，不做任何图像再设计）。"""
    master = Image.open(master_path).convert("RGBA")
    blobs = []
    for name, px in ICNS_ENTRIES:
        buf = io.BytesIO()
        if px == master.size[0]:
            master.save(buf, format="PNG")
        else:
            master.resize((px, px), Image.LANCZOS).save(buf, format="PNG")
        blobs.append((name, buf.getvalue()))
    total = 8 + sum(8 + len(b) for _, b in blobs)
    with open(out_path, "wb") as f:
        f.write(b"icns")
        f.write(struct.pack(">I", total))
        for name, data in blobs:
            f.write(name.encode("ascii"))
            f.write(struct.pack(">I", 8 + len(data)))
            f.write(data)
    return total


def main():
    here = os.path.dirname(os.path.abspath(__file__))
    repo = os.path.dirname(here)
    ap = argparse.ArgumentParser(description="FilmSheet 图标打包（仅格式转换）")
    ap.add_argument("--src", default=r"C:\Users\79242\Desktop\filmsheet_icons",
                    help="主图套件所在目录")
    args = ap.parse_args()

    src = args.src
    missing = [f for f in SET_FILES if not os.path.exists(os.path.join(src, f))]
    if missing:
        sys.exit(f"[err] 源目录缺少文件: {', '.join(missing)}")

    assets = os.path.join(repo, "assets")
    os.makedirs(assets, exist_ok=True)

    for name in SET_FILES:
        with open(os.path.join(src, name), "rb") as f:
            data = f.read()
        with open(os.path.join(assets, name), "wb") as f:
            f.write(data)
        print(f"[ok] assets/{name} ({len(data)} B)")

    icns_path = os.path.join(assets, "icon.icns")
    total = build_icns(os.path.join(src, "icon-macos-1024.png"), icns_path)
    print(f"[ok] assets/icon.icns ({total} B, 由 icon-macos-1024.png 转换)")

    for name in ("icon-192.png", "icon-512.png"):
        with open(os.path.join(src, name), "rb") as f:
            data = f.read()
        with open(os.path.join(repo, "api", name), "wb") as f:
            f.write(data)
        print(f"[ok] api/{name} ({len(data)} B)")

    print("done")


if __name__ == "__main__":
    main()
