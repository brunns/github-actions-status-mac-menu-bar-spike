from setuptools import setup

APP = ['status.py']
DATA_FILES = []
DEPENDENCIES = [
    'requests',
    'rumps',
    'furl',
    'python-box',
    'ordered-enum',
    'arrow',
    'contexttimer',
    'humanize',
]
OPTIONS = {
    'argv_emulation': True,
    'plist': {
        'LSUIElement': True,
    },
    'iconfile': 'assets/icon.icns',
}

setup(
    name="Github Actions Status",
    version="0.1.0",
    app=APP,
    data_files=DATA_FILES,
    options={'py2app': OPTIONS},
    setup_requires=['py2app'],
    install_requires=DEPENDENCIES,
)
