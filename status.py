import rumps
import requests
import webbrowser

rumps.debug_mode(True)

OK = "✅"
FAILED = "❌"
repos = [('brunns', 'mbtest'), ('brunns', 'brunns-matchers')]


class StatusApp:
    def __init__(self):
        self.app = rumps.App("Gitb Actions Status", OK)

    def run(self):
        self.app.run()
        rumps.notification(title="Starting...", subtitle="", message="")
        print("starting")

    @rumps.timer(60)
    def check(self):
        rumps.notification(title="Checking...", subtitle="", message="")
        print("checking")
        for owner, repo in repos:
            url = f"https://api.github.com/repos/{owner}/{repo}/actions/runs"
            r = requests.get(url)
            for run in r.json()['workflow_runs'][:9]:
                print(owner, repo, run['status'], run['conclusion'], run['created_at'], run['html_url'])


if __name__ == "__main__":
    app = StatusApp()
    app.run()
