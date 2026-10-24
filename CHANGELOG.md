# Changelog

All notable changes to this project are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and the project uses
[Semantic Versioning](https://semver.org/).

## [Unreleased]

### Added

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
