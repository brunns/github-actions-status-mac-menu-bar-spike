# github-actions-status-mac-menu-bar-spike

Spike for a Mac menu bar app to display Github actions run status.

This is **very** much spike code - no tests, hard-coding everywhere, and the structure is all wrong. Kinda works, though.

The list of repos to check is in `config.json`, anong with the check interval and the date format to show.

To run:
    pyenv local system
    python3 -m venv venv
    source venv/bin/activate
    pip install -r requirements.txt --upgrade
    python3 status.py -vvv

To build app:

    python3 setup.py py2app -A