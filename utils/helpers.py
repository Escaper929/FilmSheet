# -*- coding: utf-8 -*-

import os
import sys
import json
import subprocess
from PIL import ImageFont

APP_NAME = "FilmSheet"
CONFIG_FILE = "filmsheet_config.json"
MAX_HISTORY_IMAGES = 30

FILM_FORMAT_RATIOS = {
    "135": 1.5, "645": 1.25, "66": 1.0, "67": 1.167,
    "68": 1.333, "69": 1.5, "617": 2.833,
}
SUPPORTED_FORMATS = ('.jpg', '.jpeg', '.png', '.tiff', '.bmp')

LABEL_MAP = {
    'roll':       ('卷号', 'Roll No.'),
    'camera':     ('相机', 'Cam'),
    'film':       ('胶卷', 'Film'),
    'shoot_date': ('拍摄日期', 'Shot Date'),
    'dev_date':   ('冲洗日期', 'Dev Date'),
    'proc':       ('冲洗方式', 'Proc'),
    'lab':        ('冲洗地点', 'Lab'),
    'scanner':    ('扫描仪', 'Scanner')
}

INFO_LAYOUT = [
    ['roll', 'camera', 'film'],
    ['shoot_date', 'dev_date', None],
    ['proc', 'lab', 'scanner']
]

NO_COLON_FIELDS = {'roll'}

STYLE_COLORS = {
    "lightbox": {
        "canvas_bg": (255, 255, 255),
        "film_base": (18, 14, 12),
        "perf_fill": (255, 255, 255),
        "text_color": (255, 140, 60),
        "border_color": (0, 0, 0, 60),
        "info_text_color": (20, 20, 20),
        "info_label_color": (120, 120, 120),
        "pack_border": (200, 200, 200),
    },
    "contact_sheet": {
        "canvas_bg": (0, 0, 0),
        "film_base": (55, 55, 55),
        "perf_fill": (0, 0, 0),
        "text_color": (235, 235, 235),
        "text_shadow": (80, 80, 80),
        "border_color": (0, 0, 0, 255),
        "info_text_color": (255, 255, 255),
        "info_label_color": (180, 180, 180),
        "pack_border": (80, 80, 80),
    }
}

def get_system_font(size):
    candidates = []
    if os.name == 'nt':
        windir = os.environ.get('WINDIR', r'C:\Windows')
        fonts_dir = os.path.join(windir, 'Fonts')
        candidates = [
            os.path.join(fonts_dir, 'msyh.ttc'),
            os.path.join(fonts_dir, 'msyhbd.ttc'),
            os.path.join(fonts_dir, 'simhei.ttf'),
            os.path.join(fonts_dir, 'arial.ttf'),
        ]
    elif os.name == 'posix':
        candidates = [
            '/System/Library/Fonts/PingFang.ttc',
            '/System/Library/Fonts/STHeiti Light.ttc',
            '/usr/share/fonts/truetype/wqy/wqy-microhei.ttc',
            '/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf',
        ]
    for path in candidates:
        if os.path.exists(path):
            try:
                return ImageFont.truetype(path, size)
            except Exception:
                continue
    try:
        return ImageFont.load_default()
    except Exception:
        return None

def open_folder(path):
    folder = os.path.dirname(os.path.abspath(path))
    try:
        if sys.platform == 'win32':
            subprocess.Popen(['explorer', folder])
        elif sys.platform == 'darwin':
            subprocess.Popen(['open', folder])
        else:
            subprocess.Popen(['xdg-open', folder])
    except Exception:
        pass

def load_config():
    # 配置文件放到用户目录，避免权限问题
    config_path = os.path.join(os.path.expanduser("~"), CONFIG_FILE)
    if os.path.exists(config_path):
        try:
            with open(config_path, 'r', encoding='utf-8') as f:
                return json.load(f)
        except Exception:
            pass
    return {
        "pack_images": [],
        "pack_position": "left",
        "pack_border_stroke": True,
        "render_style": "lightbox",
        "start_frame": 1,
        "show_ab_marker": False,
    }

def save_config(data):
    config_path = os.path.join(os.path.expanduser("~"), CONFIG_FILE)
    try:
        with open(config_path, 'w', encoding='utf-8') as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
    except Exception:
        pass

def add_pack_image_history(path):
    cfg = load_config()
    history = cfg.get("pack_images", [])
    abs_path = os.path.abspath(path)
    if abs_path in history:
        history.remove(abs_path)
    history.insert(0, abs_path)
    history = [p for p in history if os.path.exists(p)][:MAX_HISTORY_IMAGES]
    cfg["pack_images"] = history
    save_config(cfg)
    return history