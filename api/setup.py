from setuptools import setup

APP = ['main.py']
DATA_FILES = []
OPTIONS = {
    'argv_emulation': True,
    'packages': ['PIL', 'tkinter', 'ttkthemes'],
    'includes': ['tkinter', 'PIL', 'ttkthemes'],
    'plist': {
        'CFBundleName': 'FilmSheet',
        'CFBundleDisplayName': 'FilmSheet',
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