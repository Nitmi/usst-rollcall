# usst-rollcall

USST TronClass rollcall watcher and notifier.

This project currently implements the safe first stage:

- Query `GET /api/radar/rollcalls?api_version=1.1.0`.
- Persist refreshed `X-SESSION-ID` and `session` cookie.
- Support multiple accounts with independent session files.
- Support account-specific notification overrides.
- Store seen rollcalls in SQLite to avoid duplicate notifications.
- Send notifications through console, Bark, Gotify, or email.
- Optionally submit supported rollcalls automatically. Auto sign is disabled by default.

## Install

After the package is published to PyPI, users can install and update without cloning the repository:

```bash
uv tool install usst-rollcall
uv tool upgrade usst-rollcall
usst-rollcall --help
```

Alternative install methods:

```bash
pipx install usst-rollcall
pipx upgrade usst-rollcall
```

For development from source:

```powershell
uv sync
uv run usst-rollcall init-config
uv run usst-rollcall where
```

Default config and runtime paths:

| Platform | Config path | Default runtime files |
| --- | --- | --- |
| Windows | `%LOCALAPPDATA%\usst-rollcall\config.yaml` | `%LOCALAPPDATA%\usst-rollcall\sessions\*.json`, `%LOCALAPPDATA%\usst-rollcall\state.sqlite3` |
| Linux / VPS | `~/.config/usst-rollcall/config.yaml` | `~/.config/usst-rollcall/sessions/*.json`, `~/.config/usst-rollcall/state.sqlite3` |

Override the config directory with:

```bash
export USST_ROLLCALL_CONFIG_DIR=/path/to/usst-rollcall-config
```

`usst-rollcall where` prints the active default config path.

To install the local checkout as a standalone command without the `uv run` prefix:

```powershell
uv tool install . --force
usst-rollcall --help
```

On Linux/VPS, run the same command from the project directory. If the command is not found, ensure the uv tool bin directory is in `PATH`.

## Release

This repository includes a tag-based GitHub Actions release workflow:

```bash
git tag v0.1.0
git push origin v0.1.0
```

The workflow builds the wheel/source distribution, publishes to PyPI, and creates a GitHub release with the build artifacts.

Before the first release:

- Make the GitHub repository public if you want the source code to be publicly visible.
- Create or claim the `usst-rollcall` project on PyPI.
- Configure PyPI Trusted Publishing for repository `Nitmi/usst-rollcall`, workflow `.github/workflows/release.yml`, environment `pypi`.
- Bump `version` in `pyproject.toml` before every new release tag.

## Session

For now, extract `X-SESSION-ID` from a logged-in request such as:

```text
GET https://1906.usst.edu.cn/api/radar/rollcalls?api_version=1.1.0
```

Then save it:

```powershell
uv run usst-rollcall accounts
uv run usst-rollcall session-set --account main --x-session-id "V2-..."
uv run usst-rollcall session-show
```

If you also have the `session` cookie value, pass:

```powershell
uv run usst-rollcall session-set --account main --x-session-id "V2-..." --session-cookie "V2-..."
```

## Commands

```powershell
uv run usst-rollcall poll-once
uv run usst-rollcall poll-once --account main
uv run usst-rollcall poll-once --all
uv run usst-rollcall poll-once --notify
uv run usst-rollcall poll-once --sign
uv run usst-rollcall watch
uv run usst-rollcall watch --account main
uv run usst-rollcall watch --all
uv run usst-rollcall watch --sign
uv run usst-rollcall watch --interval 5 --ticks 3
uv run usst-rollcall notify-test
uv run usst-rollcall notify-test --account main
```

Command defaults:

| Command | Default behavior |
| --- | --- |
| `accounts` | Lists configured accounts and whether each account has a notification override. |
| `session-set` | Writes session data for `--account main` unless another account is specified. |
| `session-show` | Shows session data for `--account main` unless another account is specified. |
| `poll-once` | Polls `--account main` once. It does not notify unless `--notify` is set. Auto sign follows `sign.enabled`. |
| `poll-once --all` | Polls all enabled accounts once. |
| `poll-once --sign` | Enables auto sign for this run, even if `sign.enabled` is false in config. |
| `poll-once --no-sign` | Disables auto sign for this run, even if `sign.enabled` is true in config. |
| `watch` | Watches `--account main` continuously. Auto sign follows `sign.enabled`. |
| `watch --all` | Watches all enabled accounts and uses each account's merged notification config. |
| `watch --sign` | Enables auto sign while watching, even if `sign.enabled` is false in config. |
| `watch --no-sign` | Disables auto sign while watching, even if `sign.enabled` is true in config. |
| `notify-test` | Tests the global `notify` config only. It is not `--all`. |
| `notify-test --account main` | Tests the merged notification config for `main`. |

Use the installed command name directly after `uv tool install . --force`; the examples use `uv run` only for development.

## Watch Alerts

`watch` does not stop on a polling error. If an account fails to query rollcalls, it sends an account-specific notification and keeps polling. `HTTP 401` means the stored `X-SESSION-ID` or `session` cookie is expired and must be refreshed with `session-set`.

Alert notifications are throttled per account and error type:

```yaml
watch:
  interval_seconds: 10.0
  alert_cooldown_seconds: 1800.0
  active_start: "07:30"
  active_end: "20:30"
```

With the default value, the same account receives at most one notification for the same error type every 30 minutes.

`watch` only sends rollcall API requests during the active time window. Outside `active_start` and `active_end`, the process stays alive and sleeps between ticks, but it does not query the backend. The default window is `07:30` to `20:30` in the server's local timezone.

## Auto Sign

Auto sign is off by default. Enable it in `config.yaml` only after the session for that account is valid:

```yaml
sign:
  enabled: true
  number_enabled: true
  radar_enabled: false
  notify_result: true
  device_id: your-stable-device-id
  radar_location:
    latitude: null
    longitude: null
    accuracy: 35.0
```

Supported methods:

| Method | Status |
| --- | --- |
| Number rollcall | Supported. The tool reads `GET /api/rollcall/{rollcall_id}/student_rollcalls`, extracts `number_code` / `numberCode`, then submits `PUT /api/rollcall/{rollcall_id}/answer_number_rollcall`. |
| Radar rollcall | Supported only when `radar_enabled: true` and `radar_location.latitude` / `radar_location.longitude` are configured. |
| QR code rollcall | Not implemented until a real QR sign-in capture is available. |

Per-account sign config overrides the global `sign` block:

```yaml
accounts:
  - id: main
    name: My account
    enabled: true
    session_file: sessions/main.json
    sign:
      enabled: true
      device_id: main-device-id
```

The SQLite state stores one sign result per `(account_id, rollcall_key)` to avoid repeated submits. If you need to retry a specific rollcall after changing config, delete that row from `state.sqlite3` or use a fresh state file.

## Accounts

Configure accounts in `config.yaml`:

```yaml
accounts:
  - id: main
    name: My account
    enabled: true
    session_file: sessions/main.json
  - id: friend
    name: Friend
    enabled: true
    session_file: sessions/friend.json
```

Each account has an independent `X-SESSION-ID` / cookie store. The SQLite state table uses `(account_id, rollcall_key)` as the uniqueness key, so two accounts can receive notifications for the same rollcall independently.

Account notification config can override the global `notify` block:

```yaml
notify:
  console:
    enabled: true
  bark:
    enabled: false
    key: ""

accounts:
  - id: main
    name: My account
    enabled: true
    session_file: sessions/main.json
    notify:
      bark:
        enabled: true
        key: main-bark-key

  - id: friend
    name: Friend
    enabled: true
    session_file: sessions/friend.json
    notify:
      console:
        enabled: false
      bark:
        enabled: true
        key: friend-bark-key
```

The merge behavior is shallow by channel and recursive inside each channel: omitted account fields inherit global defaults, configured fields override them. During `watch --all`, every account uses its own merged notification config.

## Notification

Edit config to enable Bark:

```yaml
notify:
  bark:
    enabled: true
    server: https://api.day.app
    key: your-bark-key
```

Console notification is enabled by default.

## Next Reverse Step

When a real rollcall is active, capture the submit request. Expected candidates:

```text
PUT /api/rollcall/{rollcall_id}/answer
PUT /api/rollcall/{rollcall_id}/answer_number_rollcall
GET /api/rollcall/{rollcall_id}/student_rollcalls
```

The current implementation covers the public number/radar endpoint patterns. A real QR sign-in capture is still needed before QR auto sign can be implemented safely.
