# github-actions-status-mac-menu-bar-spike

Spike for a Mac menu bar app to display Github actions run status.

This is **very** much spike code - no tests, hard-coding everywhere, and the structure is all wrong. Kinda works, though.

The list of repos to check is in `config.json`, along with the check interval and the date format to show. If running as an app, instead config will be found in `~/.github_actions_status_config.json` which will be created on the first run.

To run from cli:

    # If you're using pyenv, you'll need to use the system python for this. If not, I expect this is the default.
    pyenv local system  
    python3 -m venv venv
    source venv/bin/activate
    pip install -r requirements.txt --upgrade
    python3 status.py -vvv

To build as a .app bundle:

    # If you're using pyenv, you'll need to use the system python for this. If not, I expect this is the default.
    pyenv local system  
    # If you have activated the venv as above, you'll need to deactivate it to build.
    deactivate  
    python3 setup.py py2app  # .app will be found in the dist/ folder