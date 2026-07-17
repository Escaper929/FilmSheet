#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import sys
import tkinter as tk
from ttkthemes import ThemedTk
from ui.app import App

def main():
    root = ThemedTk(theme="arc")
    app = App(root)
    root.mainloop()

if __name__ == "__main__":
    main()