import os

from setuptools import setup

APP = ["status.py"]
DATA_FILES = []
DEPENDENCIES = [
    "requests==2.28.2",
    "rumps==0.4.0",
    "furl==2.1.3",
    "python-box==7.0.1",
    "ordered-enum==0.0.8",
    "arrow==1.2.3",
    "contexttimer==0.3.3",
    "humanize==4.6.0",
    "python-json-logger==2.0.7",
    "pyperclip==1.8.2",
    "aiohttp==3.8.4",
    "aiohttp_retry~=2.8.3",
]
OPTIONS = {
    "argv_emulation": True,
    "plist": {
        "LSUIElement": True,
        "LSEnvironment": {"GITHUB_OAUTH_CLIENT_ID": os.environ["GITHUB_OAUTH_CLIENT_ID"]},
    },
    "iconfile": "assets/icon.icns",
}

setup(
    name="GitHub Actions Status",
    version="0.7.0",
    app=APP,
    data_files=DATA_FILES,
    options={"py2app": OPTIONS},
    setup_requires=["py2app"],
    install_requires=DEPENDENCIES,
)
