# github-actions-status-mac-menu-bar-spike

Spike for a Mac menu bar app to display Github actions run status.

This is **very** much spike code - no tests, hard-coding everywhere, and the structure is all wrong. Kinda works, though.

The list of repos to check is in `config.json`, anong with the check interval and the date format to show.

To run:

    python3 -m venv venv  ## Only up to Python 3.9 for now - rumps doesn't work under 3.10
    source venv/bin/activate
    pip install -r requirements.txt --upgrade
    python status.py
