import os

from setuptools import setup

APP = ["status.py"]
DATA_FILES = []
DEPENDENCIES = [
    "httpx~=0.27.0",
    "rumps==0.4.0",
    "yarl~=1.9.4",
    "dataclasses-json~=0.6.4",
    "ordered-enum~=0.0.8",
    "arrow~=1.3.0",
    "contexttimer~=0.3.3",
    "humanize~=4.9.0",
    "python-json-logger~=2.0.7",
    "pyperclip~=1.8.2",
    "charset-normalizer~=3.3.2",
]
OPTIONS = {
    "argv_emulation": False,
    "plist": {
        "LSUIElement": True,
        "LSEnvironment": {"GITHUB_OAUTH_CLIENT_ID": os.environ["GITHUB_OAUTH_CLIENT_ID"]},
    },
    "iconfile": "assets/icon.icns",
    "includes": ["charset_normalizer", "imp"],
}

setup(
    name="GitHub Actions Status",
    version="0.9.1",
    app=APP,
    data_files=DATA_FILES,
    options={"py2app": OPTIONS},
    setup_requires=["py2app"],
    install_requires=DEPENDENCIES,
)
