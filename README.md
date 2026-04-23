# usst-rollcall

USST TronClass rollcall watcher and notifier.

This project currently implements the safe first stage:

- Query `GET /api/radar/rollcalls?api_version=1.1.0`.
- Persist refreshed `X-SESSION-ID` and `session` cookie.
- Support multiple accounts with independent session files.
- Store seen rollcalls in SQLite to avoid duplicate notifications.
- Send notifications through console, Bark, Gotify, or email.
- Keep the actual sign-in submit endpoint as a future extension until a real sign-in capture is available.

## Setup

```powershell
uv sync
uv run usst-rollcall init-config
uv run usst-rollcall where
```

The default config path is under `%LOCALAPPDATA%\usst-rollcall\config.yaml`.

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
uv run usst-rollcall watch
uv run usst-rollcall watch --account main
uv run usst-rollcall watch --all
uv run usst-rollcall watch --interval 5 --ticks 3
uv run usst-rollcall notify-test
```

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

After that, implement submit logic in `TronClassClient.answer_rollcall`.
