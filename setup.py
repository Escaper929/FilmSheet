from setuptools import setup

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
    'plist': {
        'CFBundleName': 'FilmSheet · 菲页',
        'CFBundleDisplayName': 'FilmSheet · 菲页',
        'CFBundleIdentifier': 'com.filmsheet.app',
        'CFBundleVersion': '1.0.0',
        'CFBundleShortVersionString': '1.0.0',
        'NSHighResolutionCapable': True,
    }
}

setup(
    app=APP,
    data_files=DATA_FILES,
    options={'py2app': OPTIONS},
    setup_requires=['py2app'],
)