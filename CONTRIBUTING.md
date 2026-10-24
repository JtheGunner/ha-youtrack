# Contributing

Thanks for your interest in improving the YouTrack integration for Home Assistant.

## Reporting issues

Open an [issue](https://github.com/JtheGunner/ha-youtrack/issues/new/choose) and include:

- Home Assistant and integration version
- YouTrack version (Cloud or self-hosted)
- What you expected and what happened
- Relevant log lines (set `custom_components.youtrack: debug` in the `logger` configuration).
  Remove tokens and private URLs before posting.

## Proposing changes

1. Open an issue first for anything larger than a small fix.
2. Branch off `main`; never commit directly to it.
3. Keep changes focused and include tests for behaviour changes.
4. Open a pull request that explains what changed and how you tested it.

## Local development

Requires [uv](https://docs.astral.sh/uv/) and Python 3.14.

```bash
scripts/setup     # create .venv, install dependencies and git hooks
scripts/check     # ruff, mypy and pytest (extra arguments go to pytest)
scripts/develop   # start Home Assistant on http://localhost:8123 with the integration mounted
```

## Commit messages

Use `<type>: <description>` in English, imperative mood, subject up to about 72 characters.
Types: `feat`, `fix`, `refactor`, `docs`, `test`, `chore`, `perf`, `ci`, `build`.

## Code of conduct

Be kind and constructive.
