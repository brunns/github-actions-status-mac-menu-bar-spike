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
from typing import (
    MutableSequence,
    Optional,
    Sequence,
    Tuple,
    Callable,
    Iterable,
    TypeVar,
    Generator,
    IO,
)

import AppKit
import arrow
import humanize
import pyperclip
import httpx
import rumps
from aiohttp.client_exceptions import ClientResponseError
from aiohttp_retry import RetryClient, FibonacciRetry, ClientResponse
from contexttimer import Timer, timer
from dataclasses_json import dataclass_json, Undefined, Exclude, config
from yarl import URL
from ordered_enum import OrderedEnum
from pythonjsonlogger import jsonlogger

TRACE = 5
logging.addLevelName(TRACE, "TRACE")

logger = logging.getLogger(__name__)

VERSION = "0.9.0"
LOCALTZ = arrow.now().tzinfo
AS_APP = getattr(sys, "frozen", None) == "macosx_app"
DEFAULT_CONFIG = json.dumps(
    {
        "repos": [
            {"owner": "brunns", "repo": "mbtest"},
            {
                "owner": "brunns",
                "repo": "brunns-matchers",
                "workflow": "ci.yml",
                "actor": "brunns",
                "branch": "master",
                "event": "push",
            },
            {"owner": "hamcrest", "repo": "PyHamcrest", "workflow": "main.yml"},
        ],
        "interval": 60,
        "verbosity": 2,
        "logfile": "/tmp/github_actions_status.log",
    },
    indent=2,
)


def main():
    try:
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
    except json.JSONDecodeError as e:
        rumps.alert("Error reading config file.", message=e.msg)
        raise

    logger.debug("config", extra=config)

    auth_holder = AuthHolder(AS_APP)
    app = StatusApp(auth_holder, debug=logger.root.level <= logging.DEBUG)

    for index, repo in enumerate(config["repos"]):
        key = str(index + 1) if index <= 8 else None
        repo = Repo.build(auth_holder=auth_holder, key=key, **repo)
        app.add(repo)

    checker = GithubActionsStatusChecker(app, auth_holder)
    timer = rumps.Timer(checker.check_all, interval)
    timer.start()

    app.run()


class Status(OrderedEnum):
    NO_RUNS = "\N{Digit Zero}\N{Variation Selector-16}\N{Combining Enclosing Keycap}"
    OK = "\N{Large Green Circle}\N{Variation Selector-16}"
    RUNNING_FROM_OK = "\N{Black Universal Recycling Symbol}\N{Variation Selector-16}"
    RUNNING_FROM_FAILED = "\N{Large Yellow Circle}\N{Variation Selector-16}"
    FAILED = "\N{Large Red Circle}\N{Variation Selector-16}"
    DISCONNECTED = "\N{No Entry Sign}\N{Variation Selector-16}"


@dataclass_json
@dataclass
class StatusApp:
    auth_holder: "AuthHolder"
    app: rumps.App = rumps.App("GitHub Actions Status", Status.OK.value, template=True)
    repos: MutableSequence["Repo"] = field(default_factory=list)
    debug: bool = False

    def run(self):
        self.app.menu.add(rumps.separator)
        self.app.menu.add(self.auth_holder.menu_item)
        self.app.run(debug=self.debug)

    def add(self, repo: "Repo"):
        self.repos.append(repo)
        self.app.menu.add(repo.menu_item)


class NoRepoRunException(Exception):
    pass


class GitHubAuthenticationException(Exception):
    pass


@dataclass_json(undefined=Undefined.EXCLUDE)
@dataclass(frozen=True)
class Actor:
    id: int
    login: str
    type: str
    site_admin: bool
    html_url: URL = field(metadata=config(decoder=URL))


@dataclass_json(undefined=Undefined.EXCLUDE)
@dataclass(frozen=True)
class Author:
    name: str
    email: str


@dataclass_json(undefined=Undefined.EXCLUDE)
@dataclass(frozen=True)
class Commit:
    id: str
    message: str
    author: Author
    committer: Author
    timestamp: arrow.Arrow = field(metadata=config(decoder=lambda d: arrow.get(d).to(LOCALTZ)))


@dataclass_json(undefined=Undefined.EXCLUDE)
@dataclass(frozen=True)
class Repository:
    """Deserialised GitHub Repository details.
    See https://docs.github.com/en/rest/repos/repos?apiVersion=2022-11-28
    """
    id: int
    name: str
    owner: Actor
    description: str
    fork: bool
    html_url: URL = field(metadata=config(decoder=URL))


@dataclass_json(undefined=Undefined.EXCLUDE)
@dataclass(frozen=True)
class WorkflowRun:
    """Deserialised GitHub Actions Workflow Run.
    See https://docs.github.com/en/rest/actions/workflow-runs?apiVersion=2022-11-28
    """

    id: int
    name: str
    actor: Actor
    triggering_actor: Actor
    event: str
    status: str
    conclusion: str
    head_commit: Commit
    repository: Repository
    run_attempt: int
    created_at: arrow.Arrow = field(metadata=config(decoder=lambda d: arrow.get(d).to(LOCALTZ)))
    updated_at: arrow.Arrow = field(metadata=config(decoder=lambda d: arrow.get(d).to(LOCALTZ)))
    run_started_at: arrow.Arrow = field(metadata=config(decoder=lambda d: arrow.get(d).to(LOCALTZ)))
    html_url: URL = field(metadata=config(decoder=URL))
    rerun_url: URL = field(metadata=config(decoder=URL))


@dataclass_json
@dataclass
class Repo:
    owner: str
    repo: str
    workflow: Optional[str]
    actor: Optional[str]
    branch: Optional[str]
    event: Optional[str]
    menu_item: rumps.MenuItem = field(
        repr=False, metadata=config(encoder=str, exclude=Exclude.ALWAYS)
    )
    auth_holder: "AuthHolder"
    workflow_name: Optional[str] = None
    status: Status = Status.DISCONNECTED
    last_run: Optional[WorkflowRun] = None
    etag: Optional[str] = None

    @classmethod
    def build(
        cls,
        owner: str,
        repo: str,
        auth_holder: "AuthHolder",
        workflow: Optional[str] = None,
        actor: Optional[str] = None,
        branch: Optional[str] = None,
        event: Optional[str] = None,
        key: Optional[str] = None,
    ) -> "Repo":
        menu_item = rumps.MenuItem(f"{owner}/{repo}", key=key)
        repo = cls(owner, repo, workflow, actor, branch, event, menu_item, auth_holder)
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
        except NoRepoRunException as e:
            logger.exception(e)
            self.status = Status.NO_RUNS
        except ClientResponseError as e:
            logger.exception(e)
            self.status = Status.DISCONNECTED
            self.etag = None

        if self.status != previous_status:
            logger.info("repo status changed", extra={"repo": self.to_dict()})

        self.menu_item.title = self.menu_title

    @property
    def menu_title(self) -> str:
        t = [f"{self.status.value} {self.owner}/{self.repo}"]
        if self.workflow:
            t.append(f" \N{Broom}{self.workflow_name}")
        if self.branch:
            t.append(f" \N{Deciduous Tree}{self.branch}")
        if self.event:
            t.append(f" \N{Party Popper}{self.event}")
        if self.actor:
            t.append(f" \N{Performing Arts}{self.actor}")
        t += [
            " - ",
            humanize.naturaldelta(arrow.now() - self.last_run.updated_at)
            if self.last_run
            else "never",
        ]
        return "".join(t)

    async def get_new_runs(self, session: RetryClient) -> Sequence[WorkflowRun]:
        """See https://docs.github.com/en/rest/actions/workflow-runs?apiVersion=2022-11-28#list-workflow-runs-for-a-repository for docs"""
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
                        logger.debug("no updates detected", extra={"repo": self.to_dict()})
                        return []
                    else:
                        resp_json = await resp.json()
                        logger.debug("updates detected", extra={"repo": self.to_dict(), "detail": resp_json})
                        if not resp_json["total_count"]:
                            raise NoRepoRunException("No repo runs detected.")
                        all_runs = [WorkflowRun.from_dict(r) for r in resp_json["workflow_runs"]]
                        logger.debug(
                            "all runs",
                            extra={"all_runs": WorkflowRun.schema().dump(all_runs, many=True)},
                        )
                        started = dropwhile(lambda r: r.status == "queued", all_runs)
                        return list(take_until(lambda r: r.status == "completed", started))

    @cached_property
    def github_api_list_workflow_runs_url(self, per_page=10) -> URL:
        """URL for getting the latest workflow runs.
        See https://docs.github.com/en/rest/actions/workflow-runs?apiVersion=2022-11-28#about-workflow-runs-in-github-actions for docs
        """
        url = URL("https://api.github.com/repos/") / self.owner / self.repo / "actions"
        if self.workflow:
            url = url / "workflows" / self.workflow
        url = url / "runs"

        url = url.update_query({"per_page": per_page})
        if self.actor:
            url = url.update_query({"actor": self.actor})
        if self.branch:
            url = url.update_query({"branch": self.branch})
        if self.event:
            url = url.update_query({"event": self.event})
        return url

    @cached_property
    def repo_url(self) -> URL:
        return URL("https://github.com/") / self.owner / self.repo

    def on_click(self, sender: rumps.MenuItem) -> None:
        event = Event.get_event()
        logger.debug("clicked", extra={"repo": self.to_dict(), "event": asdict(event)})
        if event.control:
            logger.info("rerunning failed jobs")
            self.rerun_failed_jobs()
        elif event.option:
            logger.info("opening actor", extra={"url": self.last_run.actor.html_url})
            webbrowser.open(str(self.last_run.actor.html_url))
        elif event.command:
            url = self.repo_url / "commit" / self.last_run.head_commit.id
            logger.info("opening commit", extra={"url": url})
            webbrowser.open(str(url))
        elif event.type == Event.EventType.right:
            logger.info("opening repo", extra={"url": self.repo_url})
            webbrowser.open(str(self.repo_url))
        elif self.last_run and self.last_run.html_url:
            logger.info("opening last workflow run", extra={"url": self.last_run.html_url})
            webbrowser.open(str(self.last_run.html_url))

    def rerun_failed_jobs(self) -> None:
        if self.status == Status.FAILED:
            resp = httpx.post(
                str(self.github_api_rerun_failed_jobs_url),
                headers={
                    "Authorization": f"Token {self.auth_holder.oauth_token}"
                    if self.auth_holder.oauth_token
                    else None,
                    "Accept": "application/vnd.github+json",
                    "X-GitHub-Api-Version": "2022-11-28",
                },
            )
            logger.debug("got", extra={"response": resp})
            resp.raise_for_status()
        else:
            logger.info("no workflow failure to re-run", extra={"repo": self.to_dict()})

    @cached_property
    def github_api_rerun_failed_jobs_url(self) -> URL:
        return (
            URL("https://api.github.com/repos/")
            / self.owner
            / self.repo
            / "actions/runs"
            / str(self.last_run.id)
            / "rerun-failed-jobs"
        )

    @staticmethod
    def _log_rate_limit_stats(resp: ClientResponse) -> None:
        remaining = int(resp.headers["X-RateLimit-Remaining"])
        limit = int(resp.headers["X-RateLimit-Limit"])
        reset = arrow.get(int(resp.headers["X-RateLimit-Reset"])).to(LOCALTZ)
        (logger.warning if remaining <= (limit / 4) else logger.debug)(
            "rate limit",
            extra={"limit": limit, "remaining": remaining, "reset": arrow.get(reset).to(LOCALTZ)},
        )


@dataclass_json
@dataclass(frozen=True)
class Event:
    class EventType(Enum):
        left = auto()
        right = auto()
        key = auto()

    type: EventType
    shift: bool
    control: bool
    option: bool
    command: bool

    @classmethod
    def get_event(cls) -> "Event":
        raw_event = AppKit.NSApplication.sharedApplication().currentEvent()

        if raw_event.type() in {AppKit.NSEventTypeLeftMouseUp, AppKit.NSEventTypeLeftMouseDown}:
            click = Event.EventType.left
        elif raw_event.type() in {AppKit.NSEventTypeRightMouseUp, AppKit.NSEventTypeRightMouseDown}:
            click = Event.EventType.right
        elif raw_event.type() == AppKit.NSEventTypeKeyDown:
            click = Event.EventType.key
        else:
            logger.warning("unknown event type", extra={"event": raw_event})
            click = None

        shift = bool(raw_event.modifierFlags() & AppKit.NSEventModifierFlagShift)
        control = bool(raw_event.modifierFlags() & AppKit.NSEventModifierFlagControl)
        option = bool(raw_event.modifierFlags() & AppKit.NSEventModifierFlagOption)
        command = bool(raw_event.modifierFlags() & AppKit.NSEventModifierFlagCommand)

        return cls(click, shift, control, option, command)


@dataclass_json
@dataclass
class GithubActionsStatusChecker:
    app: StatusApp
    auth_holder: "AuthHolder"

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
            tasks = []
            for repo in self.app.repos:
                task = asyncio.ensure_future(repo.check(session))
                tasks.append(task)

            responses = await asyncio.gather(*tasks)
            return responses


@dataclass_json
@dataclass
class AuthHolder:
    github_client_id: Optional[str]
    oauth_token: Optional[str]
    oauth_token_filepath: Path
    menu_item: rumps.MenuItem = field(
        repr=False, metadata=config(encoder=str, exclude=Exclude.ALWAYS)
    )

    AUTH_URL = URL("https://github.com/login/device/code")
    POLL_URL = URL("https://github.com/login/oauth/access_token")
    CHECK_URL = URL("https://api.github.com/user/issues")
    SCOPE = "repo"

    AUTHENTICATED = "\N{WHITE HEAVY CHECK MARK}\N{Variation Selector-16} Authenticated"
    AUTHENTICATE = "\N{BLACK QUESTION MARK ORNAMENT}\N{Variation Selector-16} Authenticate"
    INVALID = "\N{CROSS MARK}\N{Variation Selector-16} Invalid"
    EXPIRED = "\N{CROSS MARK}\N{Variation Selector-16} Expired"
    CANNOT_AUTHENTICATE = "\N{CROSS MARK}\N{Variation Selector-16} Cannot authenticate"

    def __init__(self, as_app: bool) -> None:
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
            menu_item_text = AuthHolder.AUTHENTICATED
        elif not self.github_client_id:
            menu_item_text = AuthHolder.CANNOT_AUTHENTICATE
        else:
            menu_item_text = AuthHolder.AUTHENTICATE
        self.menu_item = rumps.MenuItem(menu_item_text, key="a")
        if self.github_client_id:
            self.menu_item.set_callback(self.on_click)

    def on_click(self, sender: rumps.MenuItem) -> None:
        """Authenticate against GitHub using OAuth device flow.
        See https://docs.github.com/en/apps/oauth-apps/building-oauth-apps/authorizing-oauth-apps#device-flow
        """
        event = Event.get_event()
        logger.debug("clicked", extra={"AuthHolder": self.to_dict(), "event": event.to_dict()})

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
                self.menu_item.title = AuthHolder.AUTHENTICATED
                self._update_oauth_token_file()
            else:
                logger.warning("Authentication - invalid token", extra=locals())
                self.menu_item.title = AuthHolder.INVALID

    def _request_device_and_user_verification_codes(self) -> Tuple[str, int, str, URL]:
        response = httpx.post(
            AuthHolder.AUTH_URL,
            headers={"Accept": "application/json"},
            data={"client_id": self.github_client_id, "scope": AuthHolder.SCOPE},
            timeout=5,
        )
        response.raise_for_status()
        response_json = response.json()
        device_code, interval, user_code, verification_uri = (
            response_json["device_code"],
            response_json["interval"],
            response_json["user_code"],
            URL(response_json["verification_uri"]),
        )

        logger.debug("Verification codes.", extra=locals())
        return device_code, interval, user_code, verification_uri

    @staticmethod
    def _prompt_user_for_code(user_code: str, verification_uri: URL) -> bool:
        logger.warning("Authentication - prompting user.", extra=locals())
        copy = rumps.alert(
            title="GitHub Actions Status - Authentication",
            message=f"Device activation - please enter code {user_code} in the browser window which will open.",
            ok="Copy code to clipboard",
            cancel="Cancel",
        )
        if copy:
            pyperclip.copy(user_code)
            webbrowser.open(verification_uri.url)
            return True
        else:
            return False

    def _poll_until_completion(self, device_code: str, interval: int) -> str:
        while True:
            # Wait for a few seconds before polling again
            time.sleep(interval)
            logger.debug("Polling for user action.")

            # Send a request to GitHub to check if the user has authorized the app
            response = httpx.post(
                AuthHolder.POLL_URL,
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

    @staticmethod
    def _test_token(access_token: str) -> bool:
        response = httpx.get(
            AuthHolder.CHECK_URL,
            headers={"Accept": "application/json", "Authorization": f"Token {access_token}"},
            timeout=5,
        )
        logger.info("Tested token", extra={"url": AuthHolder.CHECK_URL, "response": response})
        return response.ok

    def _update_oauth_token_file(self) -> None:
        with self.oauth_token_filepath.open("w") as f:
            f.write(self.oauth_token)
            logger.info("Wrote token to file", extra={"file": self.oauth_token_filepath})

    def expired(self) -> None:
        logger.warning("token expired")
        self.oauth_token = None
        self.menu_item.title = AuthHolder.EXPIRED
        rumps.notification(
            title="Authentication",
            subtitle="Authentication Expired",
            message="GitHub authentication expired - please re-authenticate.",
        )


T = TypeVar("T")


def take_until(predicate: Callable[[T], bool], iterable: Iterable[T]) -> Generator[T, None, None]:
    for i in iterable:
        yield i
        if predicate(i):
            break


def get_config_from_config_file(config_path: Path, default: str):
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


def parse_args() -> argparse.Namespace:
    args = create_parser().parse_args()
    init_logging(args.verbosity, silence_packages=["urllib3"])

    return args


def create_parser() -> argparse.ArgumentParser:
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

    def __init__(
        self,
        mode: str = "r",
        bufsize: int = -1,
        encoding: str = None,
        errors: str = None,
        default: Optional[str] = None,
    ) -> None:
        super(FileTypeWithWrittenDefault, self).__init__(
            mode=mode, bufsize=bufsize, encoding=encoding, errors=errors
        )
        self._default = default

    def __call__(self, filepath: str) -> IO:
        path = Path(filepath)
        if filepath != "-" and self._mode == "r" and not path.is_file():
            with path.open("w") as f:
                f.write(self._default or "")
        return super(FileTypeWithWrittenDefault, self).__call__(filepath)


def init_logging(
    verbosity: int,
    handler=logging.StreamHandler(stream=sys.stdout),
    silence_packages: Sequence[str] = (),
):
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
