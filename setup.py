import os

from setuptools import setup

APP = ["status.py"]
DATA_FILES = []
DEPENDENCIES = [
    "requests==2.28.1",
    "rumps==0.4.0",
    "furl==2.1.3",
    "python-box==6.1.0",
    "ordered-enum==0.0.6",
    "arrow==1.2.3",
    "contexttimer==0.3.3",
    "humanize==4.4.0",
    "python-json-logger==2.0.4",
    "pyperclip==1.8.2",
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
    version="0.6.0",
    app=APP,
    data_files=DATA_FILES,
    options={"py2app": OPTIONS},
    setup_requires=["py2app"],
    install_requires=DEPENDENCIES,
)
