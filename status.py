#!/usr/bin/env python
import argparse
import asyncio
import json
import logging
import os
import sys
import time
import warnings
import webbrowser
from dataclasses import dataclass, field, asdict
from enum import Enum, auto
from functools import cached_property
from itertools import dropwhile
from logging.handlers import TimedRotatingFileHandler
from pathlib import Path
from typing import MutableSequence, Optional, Sequence

import AppKit
import arrow
import humanize
import pyperclip
import requests
import rumps
from aiohttp.client_exceptions import ClientResponseError
from aiohttp_retry import RetryClient, FibonacciRetry
from box import Box
from contexttimer import Timer, timer
from furl import furl
from ordered_enum import OrderedEnum
from pythonjsonlogger import jsonlogger

TRACE = 5
logging.addLevelName(TRACE, "TRACE")

logger = logging.getLogger(__name__)

VERSION = "0.8.0"
LOCALTZ = arrow.now().tzinfo
AS_APP = getattr(sys, "frozen", None) == "macosx_app"
DEFAULT_CONFIG = json.dumps(
    {
        "repos": [
            {"owner": "brunns", "repo": "mbtest"},
            {"owner": "hamcrest", "repo": "PyHamcrest", "workflow": "main.yml"},
        ],
        "interval": 60,
        "verbosity": 2,
        "logfile": "/tmp/github_actions_status.log",
    },
    indent=2,
)


def main():
    if AS_APP:
        config = get_config_from_config_file(
            Path.home() / ".github_actions_status" / "config.json", DEFAULT_CONFIG
        )
        interval = config["interval"]
    else:  # CLI
        args = parse_args()
        logger.debug("args", extra=vars(args))
        config = json.load(args.config)
        interval = args.interval or config["interval"]

    logger.debug("config", extra=config)

    auth_holder = AuthHolder(AS_APP)
    app = StatusApp(auth_holder, debug=logger.root.level <= logging.DEBUG)

    for index, repo in enumerate(config["repos"]):
        repo = Repo.build(
            key=str(index + 1) if index <= 8 else None, auth_holder=auth_holder, **repo
        )
        app.add(repo)

    checker = GithubActionsStatusChecker(app, auth_holder)
    timer = rumps.Timer(checker.check_all, interval)
    timer.start()

    app.run()


class Status(OrderedEnum):
    OK = "🟢"
    RUNNING_FROM_OK = "♻️"
    RUNNING_FROM_FAILED = "🟠"
    FAILED = "🔴"
    DISCONNECTED = "🚫"


class StatusApp:
    def __init__(self, auth_holder: "AuthHolder", debug=False):
        self.app: rumps.App = rumps.App("GitHub Actions Status", Status.OK.value)
        self.repos: MutableSequence["Repo"] = []
        self.auth_holder = auth_holder
        self.debug: bool = debug

    def run(self):
        self.app.menu.add(rumps.separator)
        self.app.menu.add(self.auth_holder.menu_item)
        self.app.run(debug=self.debug)

    def add(self, repo: "Repo"):
        self.repos.append(repo)
        self.app.menu.add(repo.menu_item)


class RepoRunException(Exception):
    pass


class GitHubAuthenticationException(Exception):
    pass


@dataclass
class Repo:
    owner: str
    repo: str
    workflow: Optional[str]
    menu_item: rumps.MenuItem = field(repr=False)
    auth_holder: "AuthHolder" = field(repr=False)
    workflow_name: Optional[str] = None
    status: Status = Status.DISCONNECTED
    last_run: Optional[Box] = None
    etag: Optional[str] = None

    @classmethod
    def build(cls, owner, repo, auth_holder, workflow=None, key=None) -> "Repo":
        menu_item = rumps.MenuItem(
            f"{owner}/{repo}/{workflow}" if workflow else f"{owner}/{repo}", key=key
        )
        repo = cls(owner, repo, workflow, menu_item, auth_holder)
        repo.menu_item.set_callback(repo.on_click)
        return repo

    async def check(self, session: RetryClient):
        previous_status = self.status
        try:
            new_runs = await self.get_new_runs(session)
            if new_runs:
                *in_progress, completed = new_runs

                self.last_run = completed

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
        except (ClientResponseError, RepoRunException) as e:
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
            humanize.naturaldelta(arrow.now() - (arrow.get(self.last_run.updated_at).to(LOCALTZ)))
            if self.last_run
            else "never"
        )
        self.menu_item.title = (
            f"{self.status.value} {self.owner}/{self.repo}/{self.workflow_name} - {last_run_formatted} ago"
            if self.workflow
            else f"{self.status.value} {self.owner}/{self.repo} - {last_run_formatted} ago"
        )

    async def get_new_runs(self, session: RetryClient) -> Sequence[Box]:  # async
        headers = {
            k: v
            for k, v in [
                (
                    "Authorization",
                    f"Token {self.auth_holder.oauth_token}"
                    if self.auth_holder.oauth_token
                    else None,
                ),
                ("If-None-Match", self.etag),
                ("Accept", "application/vnd.github+json"),
                ("X-GitHub-Api-Version", "2022-11-28"),
            ]
            if v
        }

        logging.log(TRACE, "getting", extra={"url": self.github_api_list_workflow_runs_url})
        with Timer() as t:
            async with session.get(
                str(self.github_api_list_workflow_runs_url), headers=headers
            ) as resp:
                logging.log(
                    TRACE,
                    "got",
                    extra={"url": self.github_api_list_workflow_runs_url, "elapsed": t.elapsed},
                )

                if resp.status == 401:
                    self.auth_holder.expired()
                else:
                    resp.raise_for_status()
                    self._log_rate_limit_stats(resp)
                    self.etag = resp.headers["ETag"]

                    if resp.status == 304:
                        logger.debug("no updates detected", extra={"repo": self})
                        return []
                    else:
                        logger.debug("updates detected", extra={"repo": self})
                        resp_json = await resp.json()
                        if not resp_json["total_count"]:
                            raise RepoRunException("No repo runs detected.")
                        all_runs = [Box(r) for r in resp_json["workflow_runs"]]
                        started = dropwhile(lambda r: r.status == "queued", all_runs)
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

    @cached_property
    def repo_url(self) -> furl:
        return furl("https://github.com/") / self.owner / self.repo

    def on_click(self, sender):
        event = Event.get_event()
        logger.debug("clicked", extra={"repo": self, "event": asdict(event)})
        if event.control:
            self.rerun_failed()
        elif event.type == EventType.right:
            logger.debug("opening repo", extra={"url": self.repo_url})
            webbrowser.open(self.repo_url.url)
        elif self.last_run.html_url:
            logger.debug("opening last run", extra={"url": self.last_run.html_url})
            webbrowser.open(self.last_run.html_url)

    def rerun_failed(self):
        logger.debug("rerunning failed")
        if self.status == Status.FAILED:
            url = (
                furl("https://api.github.com/repos/")
                / self.owner
                / self.repo
                / "actions/runs"
                / str(self.last_run.id)
                / "rerun-failed-jobs"
            )
            logger.debug("posting to", extra={"url": url})
            resp = requests.post(
                url,
                headers={
                    "Authorization": f"Token {self.auth_holder.oauth_token}"
                    if self.auth_holder.oauth_token
                    else None,
                    "Accept": "application/vnd.github+json",
                    "X-GitHub-Api-Version": "2022-11-28",
                },
            )
            logger.debug("got", extra={"response": resp})
        else:
            logger.debug("No failure to re-run", extra={"repo": self})

    def _log_rate_limit_stats(self, resp):
        remaining = int(resp.headers["X-RateLimit-Remaining"])
        limit = int(resp.headers["X-RateLimit-Limit"])
        reset = arrow.get(int(resp.headers["X-RateLimit-Reset"])).to(LOCALTZ)
        (logger.warning if remaining <= (limit / 4) else logger.debug)(
            "rate limit",
            extra={"limit": limit, "remaining": remaining, "reset": arrow.get(reset).to(LOCALTZ)},
        )


class EventType(Enum):
    left = auto()
    right = auto()
    key = auto()


@dataclass
class Event:
    type: EventType
    shift: bool
    control: bool
    option: bool
    command: bool

    @classmethod
    def get_event(cls) -> "Event":
        raw_event = AppKit.NSApplication.sharedApplication().currentEvent()

        if raw_event.type() == AppKit.NSEventTypeLeftMouseUp:
            click = EventType.left
        elif raw_event.type() == AppKit.NSEventTypeRightMouseUp:
            click = EventType.right
        elif raw_event.type() == AppKit.NSEventTypeKeyDown:
            click = EventType.key
        else:
            logger.warning("unknown event type", extra={"event": raw_event})
            click = None

        shift = bool(raw_event.modifierFlags() & AppKit.NSEventModifierFlagShift)
        control = bool(raw_event.modifierFlags() & AppKit.NSEventModifierFlagControl)
        option = bool(raw_event.modifierFlags() & AppKit.NSEventModifierFlagOption)
        command = bool(raw_event.modifierFlags() & AppKit.NSEventModifierFlagCommand)

        return cls(click, shift, control, option, command)


class GithubActionsStatusChecker:
    def __init__(self, app: StatusApp, auth_holder: "AuthHolder") -> None:
        self.app = app
        self.auth_holder = auth_holder

    @timer(logger=logger, level=TRACE)
    def check_all(self, sender):
        previous_overall_status = max(repo.status for repo in self.app.repos)

        asyncio.run(self._check_all())

        overall_status = max(repo.status for repo in self.app.repos)
        self.app.app.title = overall_status.value
        if overall_status is Status.DISCONNECTED:
            rumps.notification(
                title="Network error",
                subtitle="GitHub Network error",
                message="Unexpected error calling GitHub API.",
                sound=False,
            )
        elif overall_status not in (
            Status.OK,
            Status.RUNNING_FROM_OK,
        ) and previous_overall_status in (
            Status.OK,
            Status.RUNNING_FROM_OK,
        ):
            rumps.notification(
                title="Failure",
                subtitle="Workflow failure",
                message="GitHub Actions workflow run failed.",
            )

    async def _check_all(self):
        async with RetryClient(retry_options=(FibonacciRetry(attempts=5))) as session:
            # adapter = HTTPAdapter(max_retries=3)  # TODO retries
            # with requests.Session() as session:  # aiohttp.ClientSession
            #     session.mount("https://", adapter)
            tasks = []
            for repo in self.app.repos:  # asyncio.run, await asyncio.gather
                task = asyncio.ensure_future(repo.check(session))
                tasks.append(task)

            responses = await asyncio.gather(*tasks)
            return responses


class AuthHolder:
    AUTH_URL = furl("https://github.com/login/device/code")
    POLL_URL = furl("https://github.com/login/oauth/access_token")
    CHECK_URL = furl("https://api.github.com/user/issues")
    SCOPE = "repo"

    AUTHENTICATED = "✅ Authenticated"
    AUTHENTICATE = "❓ Authenticate"
    INVALID = "❌ Invalid"
    EXPIRED = "❌ Expired"
    CANNOT_AUTHENTICATE = "❌ Cannot authenticate"

    def __init__(self, as_app):
        try:
            self.github_client_id = os.environ["GITHUB_OAUTH_CLIENT_ID"]
        except KeyError:
            logger.error("Env var GITHUB_OAUTH_CLIENT_ID not found.")
            self.github_client_id = None
        self.oauth_token = None

        oauth_token_filename = ".oauth_token"
        self.oauth_token_filepath = (
            Path.home() / ".github_actions_status" / oauth_token_filename
            if as_app
            else Path(oauth_token_filename)
        )
        if self.oauth_token_filepath.is_file():
            with self.oauth_token_filepath.open("r") as f:
                self.oauth_token = f.read()
                logger.info(
                    "loaded OAuth token from file",
                    extra={"oauth_token_filepath": self.oauth_token_filepath},
                )
        else:
            logger.info(
                "OAuth token file not found",
                extra={"oauth_token_filepath": self.oauth_token_filepath},
            )

        if self.oauth_token:
            menu_item_text = self.AUTHENTICATED
        elif not self.github_client_id:
            menu_item_text = self.CANNOT_AUTHENTICATE
        else:
            menu_item_text = self.AUTHENTICATE
        self.menu_item = rumps.MenuItem(menu_item_text, key="a")
        if self.github_client_id:
            self.menu_item.set_callback(self.on_click)

    def on_click(self, sender):
        """Authenticate against GitHub using OAuth device flow.
        See https://docs.github.com/en/apps/oauth-apps/building-oauth-apps/authorizing-oauth-apps#device-flow
        """
        logger.debug("clicked", extra={"AuthHolder": self})

        (
            device_code,
            interval,
            user_code,
            verification_uri,
        ) = self._request_device_and_user_verification_codes()
        if self._prompt_user_for_code(user_code, verification_uri):
            access_token = self._poll_until_completion(device_code, interval)
            if self._test_token(access_token):
                self.oauth_token = access_token
                self.menu_item.title = self.AUTHENTICATED
                self._update_oauth_token_file()
            else:
                logger.warning("Authentication - invalid token", extra=locals())
                self.menu_item.title = self.INVALID

    def _request_device_and_user_verification_codes(self):
        response = requests.post(
            self.AUTH_URL,
            headers={"Accept": "application/json"},
            data={"client_id": self.github_client_id, "scope": self.SCOPE},
            timeout=5,
        )
        response.raise_for_status()
        response_json = response.json()
        device_code, interval, user_code, verification_uri = (
            response_json["device_code"],
            response_json["interval"],
            response_json["user_code"],
            response_json["verification_uri"],
        )

        logger.debug("Verification codes.", extra=locals())
        return device_code, interval, user_code, verification_uri

    def _prompt_user_for_code(self, user_code, verification_uri):
        logger.warning("Authentication - prompting user.", extra=locals())
        copy = rumps.alert(
            title="GitHub Actions Status - Authentication",
            message=f"Device activation - please enter code {user_code} in the browser window which will open.",
            ok="Copy code to clipboard",
            cancel="Cancel",
        )
        if copy:
            pyperclip.copy(user_code)
            webbrowser.open(verification_uri)
            return True

    def _poll_until_completion(self, device_code, interval):
        while True:
            # Wait for a few seconds before polling again
            time.sleep(interval)
            logger.debug("Polling for user action.")

            # Send a request to GitHub to check if the user has authorized the app
            response = requests.post(
                self.POLL_URL,
                headers={"Accept": "application/json"},
                data={
                    "client_id": self.github_client_id,
                    "device_code": device_code,
                    "grant_type": "urn:ietf:params:oauth:grant-type:device_code",
                },
                timeout=5,
            )
            response.raise_for_status()
            response_json = response.json()

            if "access_token" in response_json:
                logger.debug("Access token acquired")
                return response_json["access_token"]
            elif response_json.get("error", None) == "expired_token":
                logger.warning("Authentication - user token expired.", extra=locals())
                raise GitHubAuthenticationException("User token expired")

    def _test_token(self, access_token):
        response = requests.get(
            self.CHECK_URL,
            headers={"Accept": "application/json", "Authorization": f"Token {access_token}"},
            timeout=5,
        )
        logger.info("Tested token", extra={"url": self.CHECK_URL, "response": response})
        return response.ok

    def _update_oauth_token_file(self):
        with self.oauth_token_filepath.open("w") as f:
            f.write(self.oauth_token)
            logger.info("Wrote token to file", extra={"file": self.oauth_token_filepath})

    def expired(self):
        logger.warning("token expired")
        self.oauth_token = None
        self.menu_item.title = self.EXPIRED
        rumps.notification(
            title="Authentication",
            subtitle="Authentication Expired",
            message="GitHub authentication expired - please re-authenticate.",
        )


def take_until(predicate, iterable):
    for i in iterable:
        yield i
        if predicate(i):
            break


def get_config_from_config_file(config_path, default):
    if not config_path.is_file():
        config_path.parents[0].mkdir(exist_ok=True)
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
