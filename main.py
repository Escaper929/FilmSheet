#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import sys
import os

# Windows DPI awareness — must be called before any tkinter widget is created
if sys.platform == 'win32':
    import ctypes
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(1)  # per-monitor v1
    except Exception:
        try:
            ctypes.windll.user32.SetProcessDPIAware()
        except Exception:
            pass

import tkinter as tk
from ui.app import App

def _set_window_icon(root):
    """挂载应用图标（源码运行与 PyInstaller 打包后都生效）。

    assets/icon-1024.png 由 tools/make_icons.py 生成；打包后经
    sys._MEIPASS 定位。挂不上不影响使用，静默跳过。
    """
    try:
        from PIL import Image, ImageTk
        candidates = []
        meipass = getattr(sys, "_MEIPASS", None)
        if meipass:
            candidates.append(os.path.join(meipass, "assets", "icon-1024.png"))
        here = os.path.dirname(os.path.abspath(__file__))
        candidates.append(os.path.join(here, "assets", "icon-1024.png"))
        for p in candidates:
            if os.path.exists(p):
                root.iconphoto(True, ImageTk.PhotoImage(Image.open(p)))
                return
    except Exception:
        pass

def main():
    root = tk.Tk()
    _set_window_icon(root)
    app = App(root)
    root.mainloop()

if __name__ == "__main__":
    main()