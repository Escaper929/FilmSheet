# -*- coding: utf-8 -*-

import os
import sys
import json
import platform
import tempfile
import subprocess

APP_NAME = "FilmSheet"

def _config_dir():
    """Return platform-specific config directory for FilmSheet."""
    system = platform.system()
    if system == "Darwin":
        base = os.path.expanduser("~/Library/Application Support")
    elif system == "Windows":
        base = os.environ.get("APPDATA", os.path.expanduser("~"))
    else:
        base = os.environ.get("XDG_CONFIG_HOME", os.path.expanduser("~/.config"))
    return os.path.join(base, APP_NAME)
CONFIG_FILE = "filmsheet_config.json"
MAX_HISTORY_IMAGES = 30

FILM_FORMAT_RATIOS = {
    "135": 1.5,
    "645": 56/41.5,      # ≈1.35:1 (width/height for 645)
    "66": 1.0,           # 1:1
    "67": 70/56,         # 1.25:1
    "68": 76.5/56,       # ≈1.367:1
    "69": 84/56,         # 1.5:1
    "612": 112/56,       # 2:1
    "617": 168/56,       # 3:1
    "624": 224/56,       # 4:1 (new)
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
        "display_name": "灯板正片",
    },
    "contact_sheet": {
        "canvas_bg": (0, 0, 0),
        "film_base": (25, 25, 25),
        "perf_fill": (0, 0, 0),
        "text_color": (235, 235, 235),
        "text_shadow": (80, 80, 80),
        "border_color": (0, 0, 0, 255),
        "info_text_color": (255, 255, 255),
        "info_label_color": (180, 180, 180),
        "pack_border": (80, 80, 80),
        "display_name": "接触印相",
    },
}

def get_system_font(size):
    # Font loading is needed only while rendering.  Keeping Pillow out of the
    # startup import path lets the Tk window appear sooner on macOS.
    from PIL import ImageFont

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
    except OSError:
        # subprocess.Popen raises OSError if the command is not found
        pass

def load_config():
    config_path = os.path.join(_config_dir(), CONFIG_FILE)
    cfg = {
        "pack_images": [],
        "pack_position": "left",
        "pack_border_stroke": True,
        "render_style": "lightbox",
        "pack_size": 80,
        "signature": "",
        "batch_export_enabled": False,
        "templates": {},
        "current_template": "",
    }
    if os.path.exists(config_path):
        try:
            with open(config_path, 'r', encoding='utf-8') as f:
                loaded = json.load(f)
            if isinstance(loaded, dict):
                cfg.update(loaded)
            else:
                # 根节点不是对象（比如早期误写成数组）——不能 update，
                # 但也不该让整个应用起不来，按默认值走。
                _backup_bad_config(config_path, '根节点不是对象')
        except json.JSONDecodeError as e:
            # 关键：JSONDecodeError 不是 OSError 的子类，原来只except
            # (OSError, FileNotFoundError) 会让它直接抛穿 —— 配置文件一旦损坏，
            # 应用启动即崩，用户的模板和参数历史全丢。
            _backup_bad_config(config_path, str(e))
        except (OSError, FileNotFoundError):
            pass
    # Clean up stale render_style values (e.g. removed styles)
    if cfg.get("render_style") not in STYLE_COLORS:
        cfg["render_style"] = "lightbox"
    return cfg


def _backup_bad_config(reason: str, detail: str = ""):
    """把损坏的配置改名备份，绝不原地覆盖。

    有了它，配置损坏时最坏的结果是「回到默认设置」，而不是「数据被空配置
    覆盖后彻底消失」。
    """
    import time
    cfg_dir = _config_dir()
    src = os.path.join(cfg_dir, CONFIG_FILE)
    try:
        stamp = time.strftime("%Y%m%d-%H%M%S")
        dst = f"{src}.corrupt-{stamp}"
        os.replace(src, dst)
        print(f"配置文件无法解析（{detail}），已备份为 {os.path.basename(dst)}",
              file=sys.stderr)
        return dst
    except OSError:
        print(f"配置文件无法解析（{detail}）且备份失败", file=sys.stderr)
        return None

def save_config(data):
    """原子写配置文件。

    原来直接 ``open(path,'w')`` 覆写：写到一半崩溃 / 磁盘满 / 断电，会留下一个
    截断的 JSON。下次启动 load_config 抛JSONDecodeError被 except 吞掉，
    **用户的模板、参数历史、全部设置一起没了**。改成临时文件 + os.replace，
    失败时旧文件完好。

    与 archive/store.py 的写入策略一致。
    """
    config_dir = _config_dir()
    try:
        os.makedirs(config_dir, exist_ok=True)
    except OSError:
        return False
    config_path = os.path.join(config_dir, CONFIG_FILE)
    tmp_path = None
    try:
        fd, tmp_path = tempfile.mkstemp(dir=config_dir, prefix=".config-", suffix=".tmp")
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
            f.flush()
            os.fsync(f.fileno())      # 落盘再替换，断电也不丢
        os.replace(tmp_path, config_path)   # 同分区原子替换
        tmp_path = None
        return True
    except Exception:
        if tmp_path and os.path.exists(tmp_path):
            try:
                os.unlink(tmp_path)
            except OSError:
                pass
        return False

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
