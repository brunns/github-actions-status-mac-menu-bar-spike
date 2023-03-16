#!/usr/bin/env python
import argparse
import json
import logging
import os
import sys
import warnings
import webbrowser
from dataclasses import dataclass
from functools import cached_property
from itertools import dropwhile
from logging.handlers import TimedRotatingFileHandler
from pathlib import Path
from typing import MutableSequence, Optional, Sequence

import arrow
import humanize
import requests
import rumps
from box import Box
from contexttimer import Timer, timer
from furl import furl
from ordered_enum import OrderedEnum
from pythonjsonlogger import jsonlogger
from requests import HTTPError
from requests.adapters import HTTPAdapter

TRACE = 5
logging.addLevelName(TRACE, "TRACE")

logger = logging.getLogger(__name__)

VERSION = "0.5.0"
LOCALTZ = arrow.now().tzinfo
AS_APP = getattr(sys, "frozen", None) == "macosx_app"
DEFAULT_CONFIG = json.dumps(
    {
        "repos": [
            {"owner": "brunns", "repo": "mbtest"},
            {"owner": "hamcrest", "repo": "PyHamcrest", "workflow": "main.yml"},
        ],
        "oauth-token": "",
        "interval": 60,
        "verbosity": 2,
        "logfile": "",
    },
    indent=4,
)


def main():
    if AS_APP:
        config = get_config_from_config_file(".github_actions_status_config.json", DEFAULT_CONFIG)
        interval = config["interval"]
    else:  # CLI
        args = parse_args()
        logger.debug("args", extra=vars(args))
        config = json.load(args.config)
        interval = args.interval or config["interval"]

    oauth_token = os.environ.get("GITHUB_OAUTH_TOKEN", config.get("oauth-token", None))

    logger.debug("config", extra=config)

    app = StatusApp()

    for repo in config["repos"]:
        repo = Repo.build(**repo)
        app.add(repo)

    checker = GithubActionsStatusChecker(app, oauth_token)
    timer = rumps.Timer(checker.check_all, interval)
    timer.start()

    app.run(debug=logger.root.level <= logging.DEBUG)


class Status(OrderedEnum):
    OK = "🟢"
    RUNNING_FROM_OK = "♻️"
    RUNNING_FROM_FAILED = "🟠"
    FAILED = "🔴"
    DISCONNECTED = "🚫"


class StatusApp:
    def __init__(self):
        self.app = rumps.App("Github Actions Status", Status.OK.value)
        self.repos: MutableSequence["Repo"] = []

    def run(self, debug=False):
        self.app.run(debug=debug)

    def add(self, repo: "Repo"):
        self.repos.append(repo)
        self.app.menu.add(repo.menu_item)


@dataclass
class Repo:
    owner: str
    repo: str
    workflow: Optional[str]
    menu_item: rumps.MenuItem
    workflow_name: Optional[str] = None
    status: Status = Status.DISCONNECTED
    last_run_url: Optional[furl] = None
    etag: Optional[str] = None
    last_run: Optional[arrow.arrow.Arrow] = None

    @classmethod
    def build(cls, owner, repo, workflow=None) -> "Repo":
        menu_item = rumps.MenuItem(f"{owner}/{repo}/{workflow}" if workflow else f"{owner}/{repo}")
        repo = cls(owner, repo, workflow, menu_item)
        repo.menu_item.set_callback(repo.on_click)
        return repo

    def check(self, session, oauth_token):
        previous_status = self.status
        try:
            new_runs = self.get_new_runs(session, oauth_token)
            if new_runs:
                *in_progress, completed = new_runs

                self.last_run_url = furl(completed.html_url)
                self.last_run = arrow.get(completed.updated_at).to(LOCALTZ)

                if in_progress:
                    self.status = (
                        Status.RUNNING_FROM_OK
                        if completed.conclusion == "success"
                        else Status.RUNNING_FROM_FAILED
                    )
                else:
                    self.status = Status.OK if completed.conclusion == "success" else Status.FAILED

                if self.workflow:
                    self.workflow_name = completed.name
        except (HTTPError, RepoRunException) as e:
            logger.exception(e)
            self.status = Status.DISCONNECTED
            self.etag = None

        if self.status != previous_status:
            logger.info(
                "Repo status",
                extra={
                    "owner": self.owner,
                    "repo": self.repo,
                    "workflow": self.workflow,
                    "status": self.status,
                },
            )

        last_run_formatted = (
            humanize.naturaldelta(arrow.now() - self.last_run) if self.last_run else "never"
        )
        self.menu_item.title = (
            f"{self.status.value} {self.owner}/{self.repo}/{self.workflow_name} - {last_run_formatted} ago"
            if self.workflow
            else f"{self.status.value} {self.owner}/{self.repo} - {last_run_formatted} ago"
        )

    def get_new_runs(self, session, oauth_token) -> Sequence[Box]:
        headers = {
            k: v
            for k, v in [
                ("Authorization", f"Token {oauth_token}" if oauth_token else None),
                ("If-None-Match", self.etag),
                ("Accept", "application/vnd.github+json"),
                ("X-GitHub-Api-Version", "2022-11-28"),
            ]
            if v
        }

        logging.log(TRACE, "getting", extra={"url": self.github_api_list_workflow_runs_url})
        with Timer() as t:
            resp = session.get(self.github_api_list_workflow_runs_url, headers=headers, timeout=5)
        logging.log(
            TRACE,
            "got",
            extra={"url": self.github_api_list_workflow_runs_url, "elapsed": t.elapsed},
        )

        resp.raise_for_status()
        self._log_rate_limit_stats(resp)
        self.etag = resp.headers["ETag"]

        if resp.status_code == 304:
            logger.debug("no updates detected", extra={"repo": self})
            return []
        else:
            logger.debug("updates detected", extra={"repo": self})
            resp_json = resp.json()
            if not resp_json["total_count"]:
                raise RepoRunException("No repo runs detected.")
            all = [Box(r) for r in resp_json["workflow_runs"]]
            started = dropwhile(lambda r: r.status == "queued", all)
            return list(take_until(lambda r: r.status == "completed", started))

    @cached_property
    def github_api_list_workflow_runs_url(self, per_page=10) -> furl:
        # See https://docs.github.com/en/rest/actions/workflow-runs?apiVersion=2022-11-28#about-workflow-runs-in-github-actions for docs
        url = furl("https://api.github.com/repos/") / self.owner / self.repo / "actions"
        if self.workflow:
            url = url / "workflows" / self.workflow
        url = url / "runs"

        url.args["per_page"] = per_page
        return url

    def on_click(self, sender):
        logger.debug("clicked", extra={"repo": self, "opening": self.last_run_url})
        if self.last_run_url:
            webbrowser.open(self.last_run_url.url)

    def _log_rate_limit_stats(self, resp):
        remaining = int(resp.headers["X-RateLimit-Remaining"])
        limit = int(resp.headers["X-RateLimit-Limit"])
        reset = arrow.get(int(resp.headers["X-RateLimit-Reset"])).to(LOCALTZ)
        (logger.warning if remaining <= (limit / 4) else logger.debug)(
            "rate limit",
            extra={"limit": limit, "remaining": remaining, "reset": arrow.get(reset).to(LOCALTZ)},
        )


class RepoRunException(Exception):
    pass


class GithubActionsStatusChecker:
    def __init__(self, app: StatusApp, oauth_token: Optional[str]) -> None:
        self.app = app
        self.oauth_token = oauth_token

    @timer(logger=logger, level=TRACE)
    def check_all(self, sender):
        previous_status = max(repo.status for repo in self.app.repos)

        adapter = HTTPAdapter(max_retries=3)
        with requests.Session() as session:
            session.mount("https://", adapter)

            for repo in self.app.repos:
                repo.check(session, self.oauth_token)

        status = max(repo.status for repo in self.app.repos)
        self.app.app.title = status.value
        if status is Status.DISCONNECTED:
            rumps.notification(
                title="Network error",
                subtitle="Github Network error",
                message="Unexpected error calling Github API.",
                sound=False,
            )
        elif status not in (Status.OK, Status.RUNNING_FROM_OK) and previous_status in (
            Status.OK,
            Status.RUNNING_FROM_OK,
        ):
            rumps.notification(
                title="Failure",
                subtitle="Workflow failure",
                message="Github Actions workflow run failed.",
            )


def take_until(predicate, iterable):
    for i in iterable:
        yield i
        if predicate(i):
            break


def get_config_from_config_file(filename, default):
    config_path = Path.home() / filename
    if not config_path.is_file():
        with config_path.open("w") as f:
            f.write(default)
    with config_path.open("r") as f:
        config = json.load(f)
    if config.get("logfile", None):
        handler = TimedRotatingFileHandler(config["logfile"], backupCount=3)
    else:
        handler = logging.StreamHandler(stream=sys.stdout)
    init_logging(config["verbosity"], silence_packages=["urllib3"], handler=handler)
    return config


def parse_args():
    args = create_parser().parse_args()
    init_logging(args.verbosity, silence_packages=["urllib3"])

    return args


def create_parser():
    parser = argparse.ArgumentParser(description="Display status of GitHub Actions..")

    parser.add_argument(
        "-c",
        "--config",
        type=FileTypeWithWrittenDefault("r", default=DEFAULT_CONFIG),
        default="config.json",
        help="config file. Default: %(default)s",
    )
    parser.add_argument(
        "-i",
        "--interval",
        type=int,
        default=0,
        help="check interval in seconds. (Overrides value from config file if non-zero.) Default: %(default)s",
    )

    parser.add_argument(
        "-v",
        "--verbosity",
        action="count",
        default=0,
        help="specify up to four times to increase verbosity, "
        "i.e. -v to see warnings, -vv for information messages, -vvv for debug messages, or -vvvv for trace messages.",
    )
    parser.add_argument("-V", "--version", action="version", version=VERSION)

    return parser


class FileTypeWithWrittenDefault(argparse.FileType):
    as_ = """As argparse.FileType, but if read mode file doesn't exist, create it using default value."""

    def __init__(self, mode="r", bufsize=-1, encoding=None, errors=None, default=None):
        super(FileTypeWithWrittenDefault, self).__init__(
            mode=mode, bufsize=bufsize, encoding=encoding, errors=errors
        )
        self._default = default

    def __call__(self, string):
        path = Path(string)
        if string != "-" and self._mode == "r" and not path.is_file():
            with path.open("w") as f:
                f.write(self._default or "")
        return super(FileTypeWithWrittenDefault, self).__call__(string)


def init_logging(verbosity, handler=logging.StreamHandler(stream=sys.stdout), silence_packages=()):
    LOG_LEVELS = [logging.ERROR, logging.WARNING, logging.INFO, logging.DEBUG, TRACE]
    level = LOG_LEVELS[min(verbosity, len(LOG_LEVELS) - 1)]
    msg_format = "%(message)s"
    if level <= logging.DEBUG:
        warnings.filterwarnings("ignore")
        msg_format = "%(asctime)s %(levelname)-8s %(name)s %(module)s.py:%(funcName)s():%(lineno)d %(message)s"
        rumps.debug_mode(True)
    handler.setFormatter(jsonlogger.JsonFormatter(msg_format))
    logging.basicConfig(level=level, format=msg_format, handlers=[handler])

    for package in silence_packages:
        logging.getLogger(package).setLevel(max([level, logging.WARNING]))


if __name__ == "__main__":
    main()
