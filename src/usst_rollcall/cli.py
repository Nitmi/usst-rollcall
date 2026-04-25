from __future__ import annotations

from datetime import datetime
from importlib.metadata import PackageNotFoundError, version as metadata_version
from pathlib import Path
from typing import Annotated

import typer
from rich.console import Console
from rich.table import Table

from . import __version__
from .client import TronClassClient, TronClassError
from .config import AccountConfig, AppConfig, default_config_path, load_config, resolve_data_path, write_default_config
from .models import NotificationMessage, SignResult
from .notify import Notifier
from .session import SessionStore, redact
from .signer import attempt_sign
from .state import StateStore
from .watcher import build_sign_message, is_within_active_window, notify_error_once, parse_clock, poll_once, watch


app = typer.Typer(help="USST TronClass rollcall watcher.")
console = Console()


def package_version() -> str:
    try:
        return metadata_version("usst-rollcall")
    except PackageNotFoundError:
        return __version__


def version_callback(value: bool) -> None:
    if value:
        console.print(f"usst-rollcall {package_version()}")
        raise typer.Exit()


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


@app.command("version")
def version_command() -> None:
    console.print(f"usst-rollcall {package_version()}")


@app.command("accounts")
def accounts(config_path: Annotated[Path | None, typer.Option("--config", "-c")] = None) -> None:
    config, resolved_config_path = load_config(config_path)
    table = Table("ID", "Name", "Enabled", "Session File", "Notify Override")
    for account in config.accounts:
        table.add_row(
            account.id,
            account.name,
            str(account.enabled),
            str(resolve_data_path(resolved_config_path, account.session_file)),
            "yes" if account.notify else "no",
        )
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
    sign: Annotated[bool | None, typer.Option("--sign/--no-sign", help="Override auto sign for this run.")] = None,
) -> None:
    config, resolved_config_path, state_store = _load_runtime(config_path)
    accounts_to_poll = config.enabled_accounts() if all_accounts else [_select_account(config, account_id)]
    try:
        with state_store:
            for account in accounts_to_poll:
                notifier = Notifier(config.notify_for_account(account)) if notify else None
                sign_config = config.sign_for_account(account)
                if sign is not None:
                    sign_config = sign_config.model_copy(update={"enabled": sign})
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
                        if sign_config.enabled and not state_store.has_sign_result(account.id, rollcall.key):
                            try:
                                result = attempt_sign(client, rollcall, sign_config)
                            except TronClassError as error:
                                result = SignResult(
                                    attempted=True,
                                    success=False,
                                    method=rollcall.type_label,
                                    message=str(error),
                                    rollcall_id=rollcall.key,
                                )
                            state_store.mark_sign_result(account.id, rollcall.key, result)
                            if notify and notifier and sign_config.notify_result:
                                notifier.send(build_sign_message(account.name, rollcall, result))
                            console.print(f"  sign={result.method}:{result.message}")
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
    sign: Annotated[bool | None, typer.Option("--sign/--no-sign", help="Override auto sign while watching.")] = None,
    ticks: Annotated[int | None, typer.Option(help="Stop after N ticks, useful for testing.")] = None,
) -> None:
    config, resolved_config_path, state_store = _load_runtime(config_path)
    accounts_to_watch = config.enabled_accounts() if all_accounts else [_select_account(config, account_id)]
    interval_seconds = interval if interval is not None else config.watch.interval_seconds

    def on_tick(tick: int, new_count: int) -> None:
        console.print(f"tick={tick} new_rollcalls={new_count}")

    try:
        with state_store:
            if len(accounts_to_watch) == 1:
                account = accounts_to_watch[0]
                notifier = Notifier(config.notify_for_account(account))
                sign_config = config.sign_for_account(account)
                if sign is not None:
                    sign_config = sign_config.model_copy(update={"enabled": sign})
                session_store = _session_store(resolved_config_path, account)
                with TronClassClient(config.http, session_store) as client:
                    watch(
                        account.id,
                        account.name,
                        client,
                        state_store,
                        notifier,
                        interval_seconds=interval_seconds,
                        alert_cooldown_seconds=config.watch.alert_cooldown_seconds,
                        active_start=config.watch.active_start,
                        active_end=config.watch.active_end,
                        sign_config=sign_config,
                        stop_after=ticks,
                        on_tick=on_tick,
                    )
                return

            tick = 0
            active_start = parse_clock(config.watch.active_start)
            active_end = parse_clock(config.watch.active_end)
            while True:
                tick += 1
                total_new = 0
                if is_within_active_window(datetime.now().astimezone(), active_start, active_end):
                    for account in accounts_to_watch:
                        notifier = Notifier(config.notify_for_account(account))
                        sign_config = config.sign_for_account(account)
                        if sign is not None:
                            sign_config = sign_config.model_copy(update={"enabled": sign})
                        session_store = _session_store(resolved_config_path, account)
                        with TronClassClient(config.http, session_store) as client:
                            try:
                                total_new += len(
                                    poll_once(
                                        account.id,
                                        account.name,
                                        client,
                                        state_store,
                                        notifier,
                                        sign_config,
                                    )
                                )
                            except TronClassError as error:
                                notify_error_once(
                                    account.id,
                                    account.name,
                                    state_store,
                                    notifier,
                                    error,
                                    config.watch.alert_cooldown_seconds,
                                )
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
def notify_test(
    config_path: Annotated[Path | None, typer.Option("--config", "-c")] = None,
    account_id: Annotated[str | None, typer.Option("--account", "-a", help="Test account-specific notification.")] = None,
) -> None:
    config, _resolved_config_path = load_config(config_path)
    if account_id:
        account = _select_account(config, account_id)
        notify_config = config.notify_for_account(account)
        body = f"Notification channel is working for account: {account.name}."
    else:
        notify_config = config.notify
        body = "Notification channel is working."
    sent = Notifier(notify_config).send(
        NotificationMessage(
            title="USST rollcall test",
            body=body,
        )
    )
    console.print(f"Sent via: {', '.join(sent) if sent else '<none>'}")


@app.callback()
def app_callback(
    _version: Annotated[
        bool | None,
        typer.Option("--version", callback=version_callback, help="Show version and exit."),
    ] = None,
) -> None:
    return None


def main() -> None:
    app()
