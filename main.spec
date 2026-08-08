# -*- mode: python ; coding: utf-8 -*-

a = Analysis(
    ['main.py'],
    pathex=['.', 'ui', 'processor', 'engine', 'utils', 'filmsheet'],
    binaries=[],
    datas=[],
    hiddenimports=[
        'ui',
        'processor',
        'processor.renderers_135',
        'processor.renderers_120',
        'processor.config_schema',
        'processor.edge_text',
        'processor.filename_utils',
        'processor.image_pipeline',
        'engine',
        'engine.film_engine',
        'utils',
        'utils.helpers',
        'filmsheet',
        'filmsheet._version',
        'PIL',
        'PIL.Image',
        'PIL.ImageDraw',
        'PIL.ImageFont',
        'PIL.ImageOps',
        'PIL.ImageTk',
        'ttkthemes',
        'concurrent.futures',
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=['PIL._imagingtk'],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
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
)

import sys
if sys.platform == 'darwin':
    app = BUNDLE(
        exe,
        name='FilmSheet.app',
        icon=None,
        bundle_identifier='com.filmsheet.app',
        info_plist={
            'CFBundleName': 'FilmSheet',
            'CFBundleDisplayName': 'FilmSheet',
            'CFBundleIdentifier': 'com.filmsheet.app',
            'NSHighResolutionCapable': True,
        },
    )
