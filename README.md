# github-actions-status-mac-menu-bar-spike

Spike for a Mac menu bar app to display Github actions run status.

This is **very** much spike code - no tests, hard-coding everywhere, and the structure is all wrong. Kinda works, though.

The list of repos to check is hard-coded in `ststus.py`. I know, right? I'm ashamed of myself.

To run:

    python3 -m venv venv
    source venev/bin/activate
    pip install -r requirements.txt
    python status.py
