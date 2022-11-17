# TO DO

I probably won't do any of these here - this is just a spike after all - but a real implementation needs these.

* [OAuth2 authentication](https://docs.github.com/en/developers/apps/authorizing-oauth-apps#device-flow), both so we don't hit API rate limits, and so we can see private repos. See [OAuth2 links](https://pinboard.in/u:brunns/t:oauth2).
* ~~Use py2app as per [Creating macOS Apps from Python Code](https://camillovisini.com/article/create-macos-menu-bar-app-pomodoro/#creating-macos-apps-from-python-code).~~
    * ~~How to find config file if in app mode?~~
* Better structure - much better.
* ~~Configurability - repos to check, check interval.~~
    * (Including eventually a preferences UI perhaps - but a config file will do to start with).
* Deal with multiple workflows per repo.
* Pause/restart app.
* Trigger workflow runs.
* ~~What's up with the threads hanging?~~ (Might be fixed?)
* Make the logs available when running as an app
* Portable app creation

## To-be domain model

```mermaid
erDiagram
    ORG ||--o{ REPO : has
    REPO ||--|{ BRANCH : has
    REPO ||--|{ ACTOR : has
    REPO ||--o{ WORKFLOW : has
    WORKFLOW ||--o{ WORKFLOW-RUN : has
    ACTOR ||--o{ WORKFLOW-RUN : has
    BRANCH ||--o{ WORKFLOW-RUN : has
    WORKFLOW ||--|{ STATUS : has
```