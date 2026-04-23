from __future__ import annotations

from pathlib import Path
from typing import Annotated

import typer
from rich.console import Console
from rich.table import Table

from .client import TronClassClient, TronClassError
from .config import AccountConfig, AppConfig, default_config_path, load_config, resolve_data_path, write_default_config
from .models import NotificationMessage
from .notify import Notifier
from .session import SessionStore, redact
from .state import StateStore
from .watcher import poll_once, watch


app = typer.Typer(help="USST TronClass rollcall watcher.")
console = Console()


def _load_runtime(config_path: Path | None) -> tuple[AppConfig, Path, StateStore]:
    config, resolved_config_path = load_config(config_path)
    state_store = StateStore(resolve_data_path(resolved_config_path, config.state_file))
    return config, resolved_config_path, state_store


def _session_store(config_path: Path, account: AccountConfig) -> SessionStore:
    return SessionStore(resolve_data_path(config_path, account.session_file))


def _select_account(config: AppConfig, account_id: str) -> AccountConfig:
    try:
        return config.get_account(account_id)
    except KeyError as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(1) from exc


@app.command("init-config")
def init_config(
    path: Annotated[Path | None, typer.Option(help="Config file path.")] = None,
    force: Annotated[bool, typer.Option(help="Overwrite existing config.")] = False,
) -> None:
    config_path = write_default_config(path, force=force)
    console.print(f"Config written: {config_path}")


@app.command("where")
def where() -> None:
    console.print(default_config_path())


@app.command("accounts")
def accounts(config_path: Annotated[Path | None, typer.Option("--config", "-c")] = None) -> None:
    config, resolved_config_path = load_config(config_path)
    table = Table("ID", "Name", "Enabled", "Session File")
    for account in config.accounts:
        table.add_row(account.id, account.name, str(account.enabled), str(resolve_data_path(resolved_config_path, account.session_file)))
    console.print(table)


@app.command("session-set")
def session_set(
    x_session_id: Annotated[str, typer.Option(prompt=True, hide_input=True)],
    session_cookie: Annotated[str | None, typer.Option(help="Optional session cookie value.")] = None,
    account_id: Annotated[str, typer.Option("--account", "-a", help="Account ID.")] = "main",
    config_path: Annotated[Path | None, typer.Option("--config", "-c")] = None,
) -> None:
    config, resolved_config_path = load_config(config_path)
    account = _select_account(config, account_id)
    store = _session_store(resolved_config_path, account)
    cookies = {"session": session_cookie} if session_cookie else None
    store.update(x_session_id=x_session_id, cookies=cookies)
    console.print(f"Session saved for account: {account.id}")


@app.command("session-show")
def session_show(
    account_id: Annotated[str, typer.Option("--account", "-a", help="Account ID.")] = "main",
    config_path: Annotated[Path | None, typer.Option("--config", "-c")] = None,
) -> None:
    config, resolved_config_path = load_config(config_path)
    account = _select_account(config, account_id)
    store = _session_store(resolved_config_path, account)
    tokens = store.load()
    table = Table("Field", "Value")
    table.add_row("config", str(resolved_config_path))
    table.add_row("account_id", account.id)
    table.add_row("account_name", account.name)
    table.add_row("session_file", str(store.path))
    table.add_row("x_session_id", redact(tokens.x_session_id))
    table.add_row("cookies", ", ".join(tokens.cookies.keys()) or "<empty>")
    table.add_row("updated_at", str(tokens.updated_at or "<never>"))
    console.print(table)


@app.command("poll-once")
def poll_once_command(
    config_path: Annotated[Path | None, typer.Option("--config", "-c")] = None,
    account_id: Annotated[str, typer.Option("--account", "-a", help="Account ID.")] = "main",
    all_accounts: Annotated[bool, typer.Option("--all", help="Poll all enabled accounts.")] = False,
    notify: Annotated[bool, typer.Option(help="Send notification for newly seen rollcalls.")] = False,
) -> None:
    config, resolved_config_path, state_store = _load_runtime(config_path)
    accounts_to_poll = config.enabled_accounts() if all_accounts else [_select_account(config, account_id)]
    notifier = Notifier(config.notify) if notify else None
    try:
        with state_store:
            for account in accounts_to_poll:
                session_store = _session_store(resolved_config_path, account)
                with TronClassClient(config.http, session_store) as client:
                    response = client.get_rollcalls()
                    console.print(f"[{account.id}] Rollcalls: {len(response.rollcalls)}")
                    for rollcall in response.rollcalls:
                        is_new = state_store.upsert_seen(account.id, rollcall)
                        if notify and is_new and notifier:
                            body = f"Account: {account.name}\n{rollcall.model_dump_json(indent=2)}"
                            notifier.send(NotificationMessage(title="USST rollcall detected", body=body))
                            state_store.mark_notified(account.id, rollcall.key)
                        console.print(
                            f"- [{account.id}] {rollcall.key} {rollcall.display_title} "
                            f"[{rollcall.type_label}] status={rollcall.status}"
                        )
    except TronClassError as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(1) from exc


@app.command("watch")
def watch_command(
    config_path: Annotated[Path | None, typer.Option("--config", "-c")] = None,
    account_id: Annotated[str, typer.Option("--account", "-a", help="Account ID.")] = "main",
    all_accounts: Annotated[bool, typer.Option("--all", help="Watch all enabled accounts.")] = False,
    interval: Annotated[float | None, typer.Option(help="Override interval seconds.")] = None,
    ticks: Annotated[int | None, typer.Option(help="Stop after N ticks, useful for testing.")] = None,
) -> None:
    config, resolved_config_path, state_store = _load_runtime(config_path)
    accounts_to_watch = config.enabled_accounts() if all_accounts else [_select_account(config, account_id)]
    interval_seconds = interval if interval is not None else config.watch.interval_seconds
    notifier = Notifier(config.notify)

    def on_tick(tick: int, new_count: int) -> None:
        console.print(f"tick={tick} new_rollcalls={new_count}")

    try:
        with state_store:
            if len(accounts_to_watch) == 1:
                account = accounts_to_watch[0]
                session_store = _session_store(resolved_config_path, account)
                with TronClassClient(config.http, session_store) as client:
                    watch(
                        account.id,
                        account.name,
                        client,
                        state_store,
                        notifier,
                        interval_seconds=interval_seconds,
                        stop_after=ticks,
                        on_tick=on_tick,
                    )
                return

            tick = 0
            while True:
                tick += 1
                total_new = 0
                for account in accounts_to_watch:
                    session_store = _session_store(resolved_config_path, account)
                    with TronClassClient(config.http, session_store) as client:
                        total_new += len(poll_once(account.id, account.name, client, state_store, notifier))
                console.print(f"tick={tick} accounts={len(accounts_to_watch)} new_rollcalls={total_new}")
                if ticks is not None and tick >= ticks:
                    return
                import time

                time.sleep(interval_seconds)
    except KeyboardInterrupt:
        console.print("Stopped.")
    except TronClassError as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(1) from exc


@app.command("notify-test")
def notify_test(config_path: Annotated[Path | None, typer.Option("--config", "-c")] = None) -> None:
    config, _resolved_config_path = load_config(config_path)
    sent = Notifier(config.notify).send(
        NotificationMessage(
            title="USST rollcall test",
            body="Notification channel is working.",
        )
    )
    console.print(f"Sent via: {', '.join(sent) if sent else '<none>'}")


def main() -> None:
    app()
