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

# --------------------------------------------------------------------------- #
#  成片字体：本机到底该用哪个字体文件、哪个字面
# --------------------------------------------------------------------------- #
# 候选：(路径或含 `*` 的 glob, 期望家族名 或 None, 期望字重关键词 或 None)
#
# ⚠️ 三个坑叠在这一个函数里，每个单独看都不显眼：
#
# ① **路径会失效**。macOS 14 起不再提供 /System/Library/Fonts/PingFang.ttc，
#    PingFang 改由 MobileAsset 下发到
#    /System/Library/AssetsV2/com_apple_MobileAsset_Font8/<内容哈希>/AssetData/，
#    哈希随字体更新而变化 → 只能 glob。写死旧路径的后果是**静默跳到下一候选**：
#    成片上的字体、字重全变了，而没有任何提示。
#
# ② **.ttc 是一个文件装多个字面**，而 PIL 的 truetype() 默认取 index 0 —— 恰恰
#    往往不是要的那个。本机实测（`ImageFont.truetype(f, 12, index=i).getname()`）：
#        PingFang.ttc        0=HK 1=MO 2=TC **3=SC Regular** 4/7/11=Medium/Semibold
#        STHeiti Medium.ttc  0=**Heiti TC**/Medium   1=Heiti SC/Medium
#        STHeiti Light.ttc   0=**Heiti TC**/Light    1=Heiti SC/Light
#    取 index 0 等于给简体界面配**繁体字形**（实测「写」字 HK 面与 SC 面的
#    getbbox 就不一样），字重也常错一档。
#
# ③ 所以候选**不记索引**，记的是「期望家族 + 期望字重」，运行时扫 .ttc 定字面。
#    家族名对不上的候选**直接跳过**、不退回 index 0，否则第 ② 条白改。
#
# 只有 macOS/Linux 的候选带期望家族；Windows 的 msyh.ttc 不存在繁简同包问题
# （family 恒为 Microsoft YaHei），保持「取 index 0」的老行为最稳 —— 本机无法
# 验证 Windows 字体表，一旦家族名对不上就会静默掉到 SimHei，那才是真退化。
_PINGFANG_GLOB = ("/System/Library/AssetsV2/com_apple_MobileAsset_Font8/"
                  "*/AssetData/PingFang.ttc")

# 扫 .ttc 时最多试多少个字面（实际最多 12 个，扫不动会提前 break）
_MAX_FACES = 24

# 期望字重 → 可接受的字重关键词，按偏好排序。
# 不能精确匹配：.ttc 里的 style 字符串写法五花八门 —— PingFang 是 "Regular"，
# Hiragino 是 "W3"/"W6"，STHeiti 是 "Medium"/"Light"。
_STYLE_WORDS = {
    "regular":  ("regular", "w3", "w4", "book", "medium", "w5", "semibold", "light"),
    "medium":   ("medium", "w5", "semibold", "w6", "regular", "w3", "bold"),
    "semibold": ("semibold", "w6", "bold", "medium", "w5", "heavy", "black", "regular"),
    "light":    ("light", "w2", "w3", "regular", "book", "medium"),
    "bold":     ("bold", "semibold", "w6", "heavy", "black", "medium"),
}


def _font_candidates():
    """按优先级给出 (路径或 glob, 期望家族, 期望字重)。"""
    if os.name == "nt":
        fonts_dir = os.path.join(os.environ.get("WINDIR", r"C:\Windows"), "Fonts")
        return [
            (os.path.join(fonts_dir, "msyh.ttc"), None, None),
            (os.path.join(fonts_dir, "msyhbd.ttc"), None, None),
            (os.path.join(fonts_dir, "simhei.ttf"), None, None),
            (os.path.join(fonts_dir, "arial.ttf"), None, None),
        ]
    return [
        # PingFang SC 与 macOS 界面同族（旧位置 + MobileAsset 新位置）
        ("/System/Library/Fonts/PingFang.ttc", "PingFang SC", "regular"),
        (_PINGFANG_GLOB, "PingFang SC", "regular"),
        ("/System/Library/Fonts/Hiragino Sans GB.ttc", "Hiragino Sans GB", "regular"),
        # 兜底。Medium 排在 Light 前：Light 是细体，和 Windows 那边 msyh Regular
        # 的字重差得太远；两者都必须指定 Heiti **SC**（index 1），不能吃 index 0。
        ("/System/Library/Fonts/STHeiti Medium.ttc", "Heiti SC", "medium"),
        ("/System/Library/Fonts/STHeiti Light.ttc", "Heiti SC", "light"),
        # Linux
        ("/usr/share/fonts/truetype/wqy/wqy-microhei.ttc", None, None),
        ("/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc", None, None),
        ("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", None, None),
    ]


def _expand(spec):
    """把（可能含通配符的）候选路径展开成真实存在的文件列表。"""
    import glob
    if "*" in spec:
        return sorted(glob.glob(spec))
    return [spec] if os.path.exists(spec) else []


def _scan_face(path, family, style):
    """在字体文件里扫出期望家族对应的字面序号；找不到该家族则返回 None。

    family 为 None 表示「不挑家族」，直接取 index 0（用于单字面文件）。
    不记索引而扫的原因见上面 ② —— 各家 macOS 版本的 .ttc 字面顺序都不一样。
    """
    if family is None:
        return 0
    from PIL import ImageFont
    words = _STYLE_WORDS.get(style, ())
    best_rank, best_idx = len(words) + 1, None
    for idx in range(_MAX_FACES):
        try:
            name = ImageFont.truetype(path, 12, index=idx).getname()
        except Exception:
            break                       # 字面枚举到头（或文件损坏）
        if name[0] != family:
            continue                    # 家族不符 → 跳过，绝不退回 index 0
        word = (name[1] or "").lower()
        rank = next((i for i, w in enumerate(words) if w in word), len(words))
        if rank < best_rank:
            best_rank, best_idx = rank, idx
            if rank == 0:
                break
    return best_idx


# 本机该用哪个文件/字面，是**机器级常量**，解析一次即可（扫 .ttc 不便宜）。
# 元素形如 (path, index)；尚未解析过则为 None。
_font_choice = None


def _resolve_font():
    """挑出本机该用的 (字体文件, 字面索引)，全都不行时返回 (None, 0)。"""
    global _font_choice
    if _font_choice is None:
        _font_choice = (None, 0)
        for spec, family, style in _font_candidates():
            hit = None
            for path in _expand(spec):
                idx = _scan_face(path, family, style)
                if idx is not None:
                    hit = (path, idx)
                    break
            if hit:
                _font_choice = hit
                break
    return _font_choice


def get_system_font(size):
    """取一个能给中文用的字体（本机解析出的那一个）。

    Font loading is needed only while rendering.  Keeping Pillow out of the
    startup import path lets the Tk window appear sooner on macOS.
    """
    from PIL import ImageFont
    path, idx = _resolve_font()
    if path:
        try:
            return ImageFont.truetype(path, size, index=idx)
        except Exception:
            pass
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
