# github-actions-status-mac-menu-bar-spike

Spike for a Mac menu bar app to display [GitHub actions](https://pinboard.in/u:brunns/t:github-actions) run status.

This is **very** much spike code - no tests, hard-coding everywhere, and the structure is all wrong. Kinda works, though.

![image](docs/images/screenshot.png)

## CLI

To run from cli:

```shell
# If you're using pyenv, you'll need to use the system python for this. If not, I expect this is the default.
pyenv local system  
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt --upgrade
python3 status.py -vvv
```

## App

To build as a .app bundle:

```shell
# If you're using pyenv, you'll need to use the system python for this. If not, I expect this is the default.
pyenv local system  
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt --upgrade
python3 setup.py py2app  # .app will be found in the dist/ folder
```

## Configuration

If running from the command line, configuration can be found in `config.json`. When running as an app,
configuration will be found in `~/.github_actions_status_config.json` (which will be created on the first
run). Edit this file to specify repositories to monitor (with specific workflows if desired), the check interval (in 
seconds), and to specify a GitHub personal token.

A [GitHub access token](https://docs.github.com/en/authentication/keeping-your-account-and-data-secure/creating-a-personal-access-token)
can optionally be specified, either as an environment variable, `GITHUB_OAUTH_TOKEN`, or via configuration. The app will
still work without this token, though private repositories will not be viewable, and the [API rate limit](https://docs.github.com/en/rest/overview/resources-in-the-rest-api#rate-limiting)
will be quite low (and shared between all users on one IP address), meaning only a few repositories can be monitored,
and the check interval should not be set too low.

Thanks to [Freja Brunning](https://twitter.com/freja_brunning) for the icon.
