import webbrowser
from dataclasses import dataclass
from itertools import dropwhile

import arrow
import requests
import rumps
from box import Box
from furl import furl
from ordered_enum import OrderedEnum
from requests import HTTPError

rumps.debug_mode(True)

REPOS = [
    ("brunns", "mbtest"),
    ("brunns", "brunns-matchers"),
    ("brunns", "PyHamcrest"),
    ("hamcrest", "PyHamcrest"),
    ("brunns", "github-actions-status-mac-menu-bar-spike"),
]
DATE_FORMAT = "DD/MM/YY HH:mm"


def main():
    global app
    app = StatusApp()

    for owner, name in sorted(REPOS):
        repo = Repo.build(name, owner)
        app.add(repo)

    app.run()


class Status(OrderedEnum):

    OK = "🟢"
    RUNNING_FROM_OK = "♻️"
    RUNNING_FROM_FAILED = "🟠"
    FAILED = "🔴"
    DISCONNECTED = "🚫"


@rumps.timer(10)
def check(sender):
    previous_status = max(repo.status for repo in app.repos)
    for repo in app.repos:
        repo.check()

    status = max(repo.status for repo in app.repos)
    app.app.title = status.value
    if status == Status.FAILED and previous_status in (Status.OK, Status.RUNNING_FROM_OK):
        rumps.notification(
            title="Oooops...", subtitle="It's gone wrong again.", message="Now go and fix it."
        )


@dataclass
class Repo:
    owner: str
    repo: str
    menu_item: rumps.MenuItem
    status: Status = Status.OK
    actions_url: furl = None
    etag: str = None
    last_run: arrow.arrow = None

    @classmethod
    def build(cls, name, owner):
        menu_item = rumps.MenuItem(f"{owner}/{name}")
        repo = cls(owner, name, menu_item)
        repo.menu_item.set_callback(repo.on_click)
        return repo

    def check(self):
        try:
            new_runs = self.get_new_runs()
            if new_runs:
                completed, in_progress = new_runs.pop(-1), new_runs

                self.actions_url = furl(completed.html_url)
                self.last_run = arrow.get(completed.updated_at)

                if in_progress:
                    self.status = (
                        Status.RUNNING_FROM_OK
                        if completed.conclusion == "success"
                        else Status.RUNNING_FROM_FAILED
                    )
                else:
                    self.status = Status.OK if completed.conclusion == "success" else Status.FAILED
        except HTTPError as e:
            print(e)
            self.status = Status.DISCONNECTED

        self.menu_item.title = f"{self.status.value} {self.owner}/{self.repo}"
        # self.menu_item.title = f"{self.status.value} {self.owner}/{self.repo} - {self.last_run.format(DATE_FORMAT)}"

    def get_new_runs(self):
        headers = {"If-None-Match": self.etag} if self.etag else {}

        resp = requests.get(self.github_api_list_workflow_runs_url(), headers=headers)

        self._report_rate_limit_shortage(resp)

        resp.raise_for_status()
        self.etag = resp.headers["ETag"]

        if resp.status_code == 304:
            return None
        else:
            all = [Box(r) for r in resp.json()["workflow_runs"]]
            started = dropwhile(lambda r: r.status == "queued", all)
            return list(take_until(lambda r: r.status == "completed", started))

    def github_api_list_workflow_runs_url(self):
        # See https://docs.github.com/en/rest/reference/actions#list-workflow-runs-for-a-repository for docs
        url = furl("https://api.github.com/repos/") / self.owner / self.repo / "actions/runs"
        url.args["accept"] = "application/vnd.github.v3+json"
        url.args["per_page"] = 10
        return url

    def on_click(self, sender):
        if self.actions_url:
            webbrowser.open(self.actions_url.url)

    @staticmethod
    def _report_rate_limit_shortage(resp):
        remaining_ = int(resp.headers["X-RateLimit-Remaining"])
        limit_ = int(resp.headers["X-RateLimit-Limit"])
        reset_ = arrow.get(int(resp.headers["X-RateLimit-Reset"]))
        if remaining_ <= (limit_ / 3):
            print(f"rate limit remaining {remaining_}, refreshes at {reset_}")


class StatusApp:
    def __init__(self):
        self.app = rumps.App("Github Actions Status", Status.OK.value)
        self.repos = []

    def run(self):
        self.app.run()

    def add(self, repo):
        self.repos.append(repo)
        self.app.menu.add(repo.menu_item)


def take_until(predicate, iterable):
    for i in iterable:
        yield i
        if predicate(i):
            break


if __name__ == "__main__":
    main()
