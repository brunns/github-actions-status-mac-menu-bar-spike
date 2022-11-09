#!/usr/bin/env python
import argparse
import json
import logging
import sys
import warnings
import webbrowser
from dataclasses import dataclass
from itertools import dropwhile
from pathlib import Path
from typing import MutableSequence, Optional, Sequence

import arrow
import requests
import rumps
from box import Box
from furl import furl
from ordered_enum import OrderedEnum
from requests import HTTPError

logger = logging.getLogger(__name__)

VERSION = "0.3.0"
LOCALTZ = arrow.now().tzinfo
AS_APP = hasattr(sys, "frozen") and sys.frozen == 'macosx_app'
DEFAULT_CONFIG = json.dumps(
    {"repos": [{"owner": "brunns", "repo": "mbtest"}, {"owner": "hamcrest", "repo": "PyHamcrest"}],
     "interval": 15,
     "dateformat": "DD/MM/YY HH:mm",
     "verbosity": 2}, indent=4)


def main():
    if AS_APP:
        config = get_config_from_config_file('.github_actions_status_config.json', DEFAULT_CONFIG)
        interval = config["interval"]
    else:
        args = parse_args()
        logger.debug("args: %s", args)
        config = json.load(args.config)
        interval = args.interval or config["interval"]

    logger.debug("config: %s", config)

    app = StatusApp()

    for repo in config["repos"]:
        repo = Repo.build(**repo, dateformat=config["dateformat"])
        app.add(repo)

    checker = GithubActionsStatusChecker(app)
    timer = rumps.Timer(checker.check, interval)
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
    dateformat: str
    menu_item: rumps.MenuItem
    status: Status = Status.DISCONNECTED
    last_run_url: Optional[furl] = None
    etag: Optional[str] = None
    last_run: Optional[arrow.arrow.Arrow] = None

    @classmethod
    def build(cls, owner, repo, dateformat) -> "Repo":
        menu_item = rumps.MenuItem(f"{owner}/{repo}")
        repo = cls(owner, repo, dateformat, menu_item)
        repo.menu_item.set_callback(repo.on_click)
        return repo

    def check(self):
        previous_status = self.status
        try:
            new_runs = self.get_new_runs()
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
        except (HTTPError, RepoRunException) as e:
            logger.exception(e)
            self.status = Status.DISCONNECTED
            self.etag = None

        if self.status != previous_status:
            logger.info("Repo %s/%s status now %s",  self.owner, self.repo, self.status)
        # self.menu_item.title = f"{self.status.value} {self.owner}/{self.repo}"
        self.menu_item.title = f"{self.status.value} {self.owner}/{self.repo} @ {self.last_run.format(self.dateformat) if self.last_run else 'never'}"

    def get_new_runs(self) -> Sequence[Box]:
        headers = {"If-None-Match": self.etag} if self.etag else {}

        resp = requests.get(self.github_api_list_workflow_runs_url(), headers=headers)

        resp.raise_for_status()
        self._log_rate_limit_stats(resp)
        self.etag = resp.headers["ETag"]

        if resp.status_code == 304:
            logger.debug(f"no updates to %s detected", self)
            return []
        else:
            logger.debug(f"updates to %s detected", self)
            resp_json = resp.json()
            if not resp_json['total_count']:
                raise RepoRunException("No repo runs detected.")
            all = [Box(r) for r in resp_json["workflow_runs"]]
            started = dropwhile(lambda r: r.status == "queued", all)
            return list(take_until(lambda r: r.status == "completed", started))

    def github_api_list_workflow_runs_url(self) -> furl:
        # See https://docs.github.com/en/rest/reference/actions#list-workflow-runs-for-a-repository for docs
        url = furl("https://api.github.com/repos/") / self.owner / self.repo / "actions/runs"
        url.args["accept"] = "application/vnd.github.v3+json"
        url.args["per_page"] = 10
        return url

    def on_click(self, sender):
        if self.last_run_url:
            webbrowser.open(self.last_run_url.url)

    def _log_rate_limit_stats(self, resp):
        remaining = int(resp.headers["X-RateLimit-Remaining"])
        limit = int(resp.headers["X-RateLimit-Limit"])
        reset = arrow.get(int(resp.headers["X-RateLimit-Reset"])).to(LOCALTZ)
        (logger.warning if remaining <= (limit / 4) else logger.debug)("rate limit %s remaining of %s, refreshes at %s", remaining, limit, reset.format(self.dateformat))


class RepoRunException(Exception):
    pass


class GithubActionsStatusChecker:
    def __init__(self, app: StatusApp) -> None:
        self.app = app

    def check(self, sender):
        previous_status = max(repo.status for repo in self.app.repos)
        for repo in self.app.repos:
            repo.check()

        status = max(repo.status for repo in self.app.repos)
        self.app.app.title = status.value
        if status not in (Status.OK, Status.RUNNING_FROM_OK) and previous_status in (Status.OK, Status.RUNNING_FROM_OK):
            rumps.notification(
                title="Oooops...", subtitle="It's gone wrong again.", message="Now go and fix it."
            )


def take_until(predicate, iterable):
    for i in iterable:
        yield i
        if predicate(i):
            break


def get_config_from_config_file(filename, default):
    config_path = Path.home() / filename
    if not config_path.is_file():
        with config_path.open('w') as f:
            f.write(default)
    with config_path.open('r') as f:
        config = json.load(f)
    init_logging(config["verbosity"], silence_packages=["urllib3"])
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
        type=FileTypeWithWrittenDefault('r', default=DEFAULT_CONFIG),
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
        help="specify up to three times to increase verbosity, "
        "i.e. -v to see warnings, -vv for information messages, or -vvv for debug messages.",
    )
    parser.add_argument("-V", "--version", action="version", version=VERSION)

    return parser


class FileTypeWithWrittenDefault(argparse.FileType):
    as_ = """As argparse.FileType, but if read mode file doesn't exist, create it using default value."""

    def __init__(self, mode='r', bufsize=-1, encoding=None, errors=None, default=None):
        super(FileTypeWithWrittenDefault, self).__init__(mode=mode, bufsize=bufsize, encoding=encoding, errors=errors)
        self._default = default

    def __call__(self, string):
        path = Path(string)
        if string != '-' and self._mode == 'r' and not path.is_file():
            with path.open('w') as f: 
                f.write(self._default or '')
        return super(FileTypeWithWrittenDefault, self).__call__(string)


def init_logging(verbosity, stream=sys.stdout, silence_packages=()):
    LOG_LEVELS = [logging.ERROR, logging.WARNING, logging.INFO, logging.DEBUG]
    level = LOG_LEVELS[min(verbosity, len(LOG_LEVELS) - 1)]
    msg_format = "%(message)s"
    if level == logging.DEBUG:
        warnings.filterwarnings("ignore")
        msg_format = "%(asctime)s %(levelname)-8s %(name)s %(module)s.py:%(funcName)s():%(lineno)d %(message)s"
        rumps.debug_mode(True)
    logging.basicConfig(level=level, format=msg_format, stream=stream)

    for package in silence_packages:
        logging.getLogger(package).setLevel(max([level, logging.WARNING]))


if __name__ == "__main__":
    main()
