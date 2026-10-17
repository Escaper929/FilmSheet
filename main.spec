# -*- mode: python ; coding: utf-8 -*-

import sys

a = Analysis(
    ['main.py'],
    pathex=['.', 'ui', 'processor', 'engine', 'utils', 'filmsheet', 'archive'],
    binaries=[],
    datas=[('assets/icon-1024.png', 'assets')],
    hiddenimports=[
        'ui',
        'ui.rounded',
        'ui.icons',
        'ui.archive_page',
        # ui.aa 是抗锯齿形状生成器。CanvasAA 在 PillButton/RoundedCard/
        # SegmentedControl 的 __init__ 里是**函数体内导入**，PIL.ImageTk 更是
        # 运行时按需 import —— 静态分析极易漏掉，漏了会在打包后启动即崩。
        'ui.aa',
        'processor',
        'processor.renderers_135',
        'processor.renderers_120',
        'processor.renderer',
        'processor.config_schema',
        'processor.edge_text',
        'processor.filename_utils',
        'processor.image_pipeline',
        'processor.film_processor',
        'engine',
        'engine.film_engine',
        'utils',
        'utils.helpers',
        'filmsheet',
        'filmsheet._version',
        # archive 包必须显式列出：ui/app.py 里的导入是模块级的，PyInstaller
        # 能静态扫到，但 archive_page.py 里对 thumbs / store 的引用有部分是
        # 在函数体内延迟取属性（如 thumbs_mod.clear_thumb），静态分析扫不到。
        'archive',
        'archive.models',
        'archive.store',
        'archive.service',
        'archive.thumbs',
        'PIL',
        'PIL.Image',
        'PIL.ImageDraw',
        'PIL.ImageFont',
        'PIL.ImageOps',
        'PIL.ImageTk',
        'PIL._imagingtk',
        'ttkthemes',
        'concurrent.futures',
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='FilmSheet',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    # Windows: exe 资源图标；macOS 的 Dock 图标走 BUNDLE 的 icon（.icns）
    icon=None if sys.platform == 'darwin' else 'assets/icon.ico',
)

if sys.platform == 'darwin':
    # A directory-style app keeps native libraries in Contents/Frameworks.
    # Unlike a one-file executable it does not unpack Pillow/Tk to a temporary
    # directory on every launch, which materially improves macOS startup time.
    app = BUNDLE(
        exe,
        a.binaries,
        a.zipfiles,
        a.datas,
        name='FilmSheet.app',
        icon='assets/icon.icns',
        bundle_identifier='com.filmsheet.app',
        info_plist={
            'CFBundleName': 'FilmSheet · 菲页',
            'CFBundleDisplayName': 'FilmSheet · 菲页',
            'CFBundleIdentifier': 'com.filmsheet.app',
            'NSHighResolutionCapable': True,
        },
    )
else:
    coll = COLLECT(
        exe,
        a.binaries,
        a.zipfiles,
        a.datas,
        strip=False,
        upx=True,
        upx_exclude=[],
        name='FilmSheet',
    )
