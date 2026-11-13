import os
import sys

from setuptools import setup

# 版本号只认 filmsheet/_version.py 一处；这里写死会与 CI 改版本的动作脱节，
# 打包出来的 .app 会一直显示旧版本（之前就是钉在 1.0.0）。
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from filmsheet._version import __VERSION__

APP = ['main.py']
DATA_FILES = []
OPTIONS = {
    'argv_emulation': True,
    # 必须列出全部自研包：py2app 只按packages 递归收集，
    # 漏掉archive 会让打包后的 App 在 import 阶段直接崩。
    'packages': ['PIL', 'tkinter', 'ttkthemes',
                 'ui', 'processor', 'engine', 'utils', 'filmsheet', 'archive'],
    'includes': ['tkinter', 'PIL', 'PIL.ImageTk', 'ttkthemes',
                 'ui.rounded', 'ui.icons', 'ui.archive_page', 'ui.aa',
                 'archive.models', 'archive.store', 'archive.service',
                 'archive.thumbs'],
    'iconfile': 'assets/icon.icns',
    'plist': {
        'CFBundleName': 'FilmSheet · 菲页',
        'CFBundleDisplayName': 'FilmSheet · 菲页',
        'CFBundleIdentifier': 'com.filmsheet.app',
        'CFBundleVersion': __VERSION__,
        'CFBundleShortVersionString': __VERSION__,
        'NSHighResolutionCapable': True,
    }
}

setup(
    app=APP,
    data_files=DATA_FILES,
    options={'py2app': OPTIONS},
    setup_requires=['py2app'],
)