from dataclasses import dataclass
from ordered_enum import OrderedEnum

import rumps
import requests
import webbrowser
from furl import furl
from box import Box

rumps.debug_mode(True)

REPOS = [('brunns', 'mbtest'), ('brunns', 'brunns-matchers'), ("brunns", "PyHamcrest"), ("hamcrest", "PyHamcrest"), ("brunns", "github-actions-status-mac-menu-bar-spike")]


def main():
    global app
    app = StatusApp()

    for owner, name in sorted(REPOS):
        menu_item = rumps.MenuItem(f"{owner}/{name}")
        repo = Repo(owner, name, menu_item)
        repo.menu_item.set_callback(repo.on_click)
        app.add(repo)

    app.run()


class Status(OrderedEnum):

    OK = "🟢"
    RUNNING = "🟠"
    FAILED = "🔴"


@rumps.timer(90 * len(REPOS))
def check(self):
    for repo in app.repos:
        repo.check()

    status = max(repo.status for repo in app.repos)
    app.app.title = status.value
    if status == Status.FAILED:
        rumps.notification(title="Oooops...", subtitle="It's gone wrong again.", message="Now go and fix it.")


@dataclass
class Repo:
    owner: str
    repo: str
    menu_item: rumps.MenuItem
    status: Status = Status.OK
    url: str = None

    def check(self):
        run = self.get_run()
        self.url = run.html_url
        self.status = Status.OK if run.conclusion == "success" else Status.FAILED
        self.menu_item.title = f"{self.status.value} {self.owner}/{self.repo}"

    def get_run(self):
        resp = requests.get(self.github_api_url())
        resp.raise_for_status()
        return Box(resp.json()['workflow_runs'][0])

    def github_api_url(self):
        url = furl("https://api.github.com/repos/") / self.owner / self.repo / "actions/runs"
        url.args['accept'] = "application/vnd.github.v3+json"
        url.args['status'] = "completed"
        url.args['per_page'] = 1
        return url

    def on_click(self, foo):
        if self.url: webbrowser.open(self.url)


class StatusApp:
    def __init__(self):
        self.app = rumps.App("Github Actions Status", Status.OK.value)
        self.repos = []

    def run(self):
        self.app.run()

    def add(self, repo):
        self.repos.append(repo)
        self.app.menu.add(repo.menu_item)


if __name__ == "__main__":
    main()
