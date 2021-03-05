from dataclasses import dataclass

import rumps
import requests
import webbrowser
from furl import furl
from box import Box

rumps.debug_mode(True)

OK = "🟢"
FAILED = "🔴"
RUNNING = "🟠"
REPOS = [('brunns', 'mbtest'), ('brunns', 'brunns-matchers')]


def main():
    app = StatusApp()

    for owner, name in REPOS:
        menu_item = rumps.MenuItem(f"{owner}/{name}")
        repo = Repo(owner, name, menu_item)
        repo.menu_item.set_callback(repo.on_click)
        app.add(repo)

    app.run()

    @rumps.timer(90 * len(REPOS))
    def check(self):
        for repo in app.repos:
            repo.check()

        status = OK if all(repo.conclusion == "success" for repo in app.repos) else FAILED
        app.app.title = status
        if status == FAILED: rumps.notification(title="Oooops...", subtitle="You fucked it up again.", message="Now go and fix it.")


@dataclass
class Repo:
    owner: str
    repo: str
    menu_item: rumps.MenuItem
    conclusion: str = None
    url: str = None

    def check(self):
        run = self.get_run()
        self.url = run.html_url
        self.conclusion = run.conclusion
        state = OK if run.conclusion == "success" else FAILED
        self.menu_item.title = f"{state} {self.owner}/{self.repo}"

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
        self.app = rumps.App("Github Actions Status", OK)
        self.repos = []

    def run(self):
        self.app.run()

    def add(self, repo):
        self.repos.append(repo)
        self.app.menu.add(repo.menu_item)


if __name__ == "__main__":
    main()
