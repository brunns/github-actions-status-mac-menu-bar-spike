# github-actions-status-mac-menu-bar-spike

Spike for a Mac menu bar app to display Github actions run status.

This is **very** much spike code - no tests, hard-coding everywhere, and the structure is all wrong. Kinda works, though.

## CLI

To run from cli:

```shell
# If you're using pyenv, you'll need to use the system python for this. If not, I expect this is the default.
pyenv local system  
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt --upgrade
python3 status.py -vvv
```

The list of repos to check is in `config.json`, along with the check interval (in seconds) and the date format to show.

A [Github access token](https://docs.github.com/en/authentication/keeping-your-account-and-data-secure/creating-a-personal-access-token) 
can optionally be specified, either as an environment variable, `GITHUB_OAUTH_TOKEN`, or via `config.json`. The app will 
still work without this token, though private repositories will not be viewable, and the [API rate limit](https://docs.github.com/en/rest/overview/resources-in-the-rest-api#rate-limiting) 
will be quite low (and shared between all users on one IP address), meaning only a few repositories can be monitored, 
and the check interval should not be set too low.

## App

To build as a .app bundle:

```shell
# If you're using pyenv, you'll need to use the system python for this. If not, I expect this is the default.
pyenv local system  
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt --upgrade
python3 setup.py py2app  # .app will be found in the dist/ folder
```

If running as an app, config will be found in `~/.github_actions_status_config.json` (which will be created on the first 
run). Edit this file to specify repositories to monitor, and to specify a Github personal token if desired.

Thanks to [Freja Brunning](https://twitter.com/freja_brunning) for the icon.
