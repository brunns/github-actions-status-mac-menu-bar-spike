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
]
OPTIONS = {
    'argv_emulation': True,
    'plist': {
        'LSUIElement': True,
    },
}

setup(
    name="Github_Actions_Status",
    app=APP,
    data_files=DATA_FILES,
    options={'py2app': OPTIONS},
    setup_requires=['py2app'],
    install_requires=DEPENDENCIES,
)
