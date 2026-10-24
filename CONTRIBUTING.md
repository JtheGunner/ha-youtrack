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

## Commit messages

Use `<type>: <description>` in English, imperative mood, subject up to about 72 characters.
Types: `feat`, `fix`, `refactor`, `docs`, `test`, `chore`, `perf`, `ci`, `build`.

## Code of conduct

Be kind and constructive.
