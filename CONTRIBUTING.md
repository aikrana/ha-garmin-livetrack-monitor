# Contributing

Thanks for your interest in improving Garmin LiveTrack Monitor. This document is short on purpose — open an issue if anything is unclear.

## Reporting bugs

Please use the **Bug report** issue template. Before submitting, enable debug logging and attach a diagnostics dump:

- Developer Tools → Actions → `logger.set_level` with:
  ```yaml
  action: logger.set_level
  data:
    custom_components.garmin_livetrack: debug
  ```
- Settings → Devices & Services → *Garmin LiveTrack Monitor* → ⋮ → **Download diagnostics**.

The diagnostics file is automatically sanitized: your password, tokens, session IDs and coordinates are redacted before download.

## Proposing changes

1. Open an issue first if the change is non-trivial — it avoids wasted work and lets us discuss approach.
2. Fork, branch off `main`, make focused commits.
3. Keep the change aligned with existing patterns in the codebase: async everywhere, constants in `const.py`, dispatcher signals for entity updates.
4. If you touch `manifest.json`, `strings.json` or `translations/*.json`, run a local HA instance with your fork to verify nothing breaks the config flow.
5. Open a PR. The `Validate` workflow runs `hassfest` and `hacs/action` on every PR — both must be green before merge.

## Supported Python / Home Assistant versions

- Minimum Home Assistant: declared in `hacs.json` (`homeassistant` key).
- Minimum Python: inherited from the supported HA version.

## Translations

Entity labels, config flow strings, and error messages live in:

- `custom_components/garmin_livetrack/strings.json` — English, source of truth.
- `custom_components/garmin_livetrack/translations/<lang>.json` — per-language files.

New languages are welcome. Copy `translations/en.json`, rename to your language code, translate the values (never the keys), and open a PR.

## Release process (maintainers)

1. Bump `version` in `custom_components/garmin_livetrack/manifest.json`.
2. Update `CHANGELOG.md`.
3. Tag the commit: `git tag vX.Y.Z && git push origin vX.Y.Z`.
4. Create a GitHub Release from the tag, paste the matching CHANGELOG entry as release notes. HACS picks up the new release automatically.
