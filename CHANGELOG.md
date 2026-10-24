# Changelog

All notable changes to this project are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and the project uses
[Semantic Versioning](https://semver.org/).

## [1.1.3] - 2026-10-25

### Changed

- The README installation section leads with searching for YouTrack in HACS and keeps the custom repository steps as a fallback.

## [1.1.2] - 2026-10-24

### Fixed

- Removing query sensors uses a checkbox list, so it is clear which sensors are selected, and submitting
  without a selection shows a hint instead of silently doing nothing.

## [1.1.1] - 2026-10-24

### Security

- The Assist search tools reject searches with unbalanced brackets, which could have escaped the restriction to the
  configured projects, and drop any result from a project that is not set up.

## [1.1.0] - 2026-10-24

### Added

- An LLM API for Assist conversation agents: search issues, search and read knowledge base articles, and,
  only when allowed in the options, create issues.
- Opt-in watched issues: a search (a tag or issue ids) whose unresolved matches each get a sensor, at most 25.
  Sensors of resolved or no longer matching issues are removed.
- A calendar per project with the due dates of unresolved issues, for projects that have a `Due Date` field.
- Diagnostics include the state of each project's knowledge base.
- README: use cases and known limitations.
- Gold quality scale declared in the manifest.
- Evaluation of push updates through a YouTrack workflow (`docs/webhook-evaluation.md`).

## [1.0.0] - 2026-10-24

First stable release: the entities, actions and options are now covered by semantic versioning.

### Changed

- At most 6 requests to YouTrack run at the same time, regardless of the number of projects.

### Added

- Brand assets (icon and logo, light and dark) shown in Home Assistant and HACS.
- A repair issue when a configured project no longer exists in YouTrack; confirming it removes the project.
  The remaining projects keep loading.
- Renamed projects are picked up automatically.
- Devices of projects that are no longer set up can be deleted from the device page.

## [0.3.0] - 2026-10-24

### Added

- Knowledge base sensors per project: article count and the last updated article with the latest five as attributes.
- Actions `youtrack.search_articles` and `youtrack.get_article`, which return their result as a response.
- Example: a script that looks up a knowledge base article and sends it as a notification.

## [0.2.0] - 2026-10-24

### Added

- Examples: a project dashboard and two automation blueprints (notify on more critical issues, create an
  issue for an entity that stays unavailable).
- Actions: `youtrack.create_issue`, `add_comment`, `transition`, `assign` and `log_work`.
- To-do lists: the open issues of each project and the issues of each query sensor. Checking or unchecking
  an item changes the issue state (configurable); creating issues from new items is an opt-in option.
- Issue activity events: an event entity per project and `youtrack_issue_*` bus events for created,
  updated, resolved, reopened and commented issues. Existing history is not replayed.
- Unresolved issues of each project are cached and refreshed with small `updated:` delta requests; a full
  reload every 12 polls drops deleted and moved issues.
- Query sensors: add, edit and remove named YouTrack searches per project. Each one is a sensor with the
  match count and the first issues as attributes.

## [0.1.0] - 2026-10-24

First release.

### Added

- Setup of a YouTrack instance with URL and permanent token, including reauthentication and reconfiguration.
- Projects are added as subentries; every project is a device.
- Issue count sensors per project: total, open, resolved, unassigned, overdue, created and resolved in the
  last 7 days, critical open.
- Binary sensors for overdue issues and critical open issues.
- Configurable polling interval (1 to 60 minutes).
- Diagnostics with redacted secrets, English and German translations.
