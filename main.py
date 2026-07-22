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
from ttkthemes import ThemedTk
from ui.app import App

def main():
    root = ThemedTk(theme="arc")
    app = App(root)
    root.mainloop()

if __name__ == "__main__":
    main()