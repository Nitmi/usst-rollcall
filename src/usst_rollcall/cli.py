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
from .config import AccountConfig, AppConfig, LoginConfig, NotifyConfig, SignConfig, default_config_path, load_config, resolve_data_path, write_default_config
from .login import LoginError, login as login_with_form
from .models import LoginResult, NotificationMessage, SignResult
from .notify import Notifier
from .session import SessionStore
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


def _sign_config_for_account(config: AppConfig, account: AccountConfig, override: bool | None) -> SignConfig:
    sign_config = account.sign
    if override is None:
        return sign_config
    return sign_config.model_copy(update={"enabled": override})


def _login_config_for_account(config: AppConfig, account: AccountConfig) -> LoginConfig:
    return account.login


def _notify_config_for_account(config: AppConfig, account: AccountConfig) -> NotifyConfig:
    return account.notify


def _sign_override_label(value: bool | None) -> str:
    if value is True:
        return "enabled by --sign"
    if value is False:
        return "disabled by --no-sign"
    return "from config"


def _notify_channels_label(notify_config: NotifyConfig) -> str:
    channels: list[str] = []
    if notify_config.console.enabled:
        channels.append("console")
    if notify_config.bark.enabled:
        channels.append("bark")
    if notify_config.gotify.enabled:
        channels.append("gotify")
    if notify_config.email.enabled:
        channels.append("email")
    return ", ".join(channels) if channels else "none"


def _print_watch_start(
    config: AppConfig,
    accounts: list[AccountConfig],
    *,
    all_accounts: bool,
    account_id: str,
    interval_seconds: float,
    sign_override: bool | None,
) -> None:
    scope = "all enabled accounts (--all active)" if all_accounts else f"single account: {account_id} (--all not active)"
    console.print("[bold]USST rollcall watch started[/bold]")
    console.print(f"Version: {package_version()}")
    console.print(f"Scope: {scope}")
    console.print(f"Auto sign: {_sign_override_label(sign_override)}")
    console.print(f"Active window: {config.watch.active_start}-{config.watch.active_end}")
    console.print(f"Interval: {interval_seconds:g}s")

    table = Table("Account", "Name", "Auto Login", "Auto Sign", "Notify")
    for account in accounts:
        sign_config = _sign_config_for_account(config, account, sign_override)
        login_config = _login_config_for_account(config, account)
        notify_config = _notify_config_for_account(config, account)
        table.add_row(
            account.id,
            account.name,
            "enabled" if login_config.enabled else "disabled",
            "enabled" if sign_config.enabled else "disabled",
            _notify_channels_label(notify_config),
        )
    console.print(table)


def _run_login(
    config: AppConfig,
    account: AccountConfig,
    session_store: SessionStore,
    *,
    force: bool = False,
) -> LoginResult:
    login_config = _login_config_for_account(config, account)
    if force:
        login_config = login_config.model_copy(update={"enabled": True})
    try:
        result = login_with_form(config.http, login_config, session_store)
    except LoginError as exc:
        return LoginResult(success=False, message=str(exc))
    return result


def _try_relogin(
    config: AppConfig,
    account: AccountConfig,
    session_store: SessionStore,
) -> bool:
    result = _run_login(config, account, session_store)
    if result.success:
        profile = result.profile_name or result.profile_id or "unknown"
        console.print(f"[{account.id}] auto login succeeded: {profile}")
        return True
    console.print(f"[yellow][{account.id}] auto login failed: {result.message}[/yellow]")
    return False


def _ensure_account_session(
    config: AppConfig,
    account: AccountConfig,
    session_store: SessionStore,
) -> None:
    if not session_store.load().is_empty():
        return
    login_config = _login_config_for_account(config, account)
    if not login_config.enabled:
        console.print(
            f"[red][{account.id}] no cached session and login.enabled is false. "
            "Configure account password login first.[/red]"
        )
        raise typer.Exit(1)
    console.print(f"[{account.id}] no cached session, signing in with account password...")
    if not _try_relogin(config, account, session_store):
        raise typer.Exit(1)


def _process_rollcalls(
    account: AccountConfig,
    response_rollcalls: list,
    *,
    state_store: StateStore,
    notifier: Notifier | None,
    notify: bool,
    sign_config: SignConfig,
    client: TronClassClient,
) -> None:
    console.print(f"[{account.id}] Rollcalls: {len(response_rollcalls)}")
    for rollcall in response_rollcalls:
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
    table = Table("ID", "Name", "Enabled", "Session File", "Auto Login", "Auto Sign", "Notify")
    for account in config.accounts:
        login_config = _login_config_for_account(config, account)
        sign_config = _sign_config_for_account(config, account, None)
        notify_config = _notify_config_for_account(config, account)
        table.add_row(
            account.id,
            account.name,
            str(account.enabled),
            str(resolve_data_path(resolved_config_path, account.session_file)),
            "enabled" if login_config.enabled else "disabled",
            "enabled" if sign_config.enabled else "disabled",
            _notify_channels_label(notify_config),
        )
    console.print(table)


@app.command("login-status")
def login_status(
    account_id: Annotated[str, typer.Option("--account", "-a", help="Account ID.")] = "main",
    config_path: Annotated[Path | None, typer.Option("--config", "-c")] = None,
) -> None:
    config, resolved_config_path = load_config(config_path)
    account = _select_account(config, account_id)
    login_config = _login_config_for_account(config, account)
    store = _session_store(resolved_config_path, account)
    tokens = store.load()
    table = Table("Field", "Value")
    table.add_row("config", str(resolved_config_path))
    table.add_row("account_id", account.id)
    table.add_row("account_name", account.name)
    table.add_row("login_enabled", str(login_config.enabled))
    table.add_row("username", login_config.username or "<empty>")
    table.add_row(
        "password_source",
        f"env:{login_config.password_env}" if login_config.password_env else ("inline" if login_config.password else "<empty>"),
    )
    table.add_row("session_cache", str(store.path))
    table.add_row("cached_session", "yes" if not tokens.is_empty() else "no")
    table.add_row("cache_updated_at", str(tokens.updated_at or "<never>"))
    console.print(table)


@app.command("login")
def login_command(
    account_id: Annotated[str, typer.Option("--account", "-a", help="Account ID.")] = "main",
    config_path: Annotated[Path | None, typer.Option("--config", "-c")] = None,
) -> None:
    config, resolved_config_path = load_config(config_path)
    account = _select_account(config, account_id)
    session_store = _session_store(resolved_config_path, account)
    result = _run_login(config, account, session_store, force=True)
    if not result.success:
        console.print(f"[red]Login failed for account {account.id}: {result.message}[/red]")
        raise typer.Exit(1)
    profile = result.profile_name or result.profile_id or "<unknown>"
    console.print(f"Login succeeded for account {account.id}: {profile}")


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
                notifier = Notifier(_notify_config_for_account(config, account)) if notify else None
                sign_config = _sign_config_for_account(config, account, sign)
                session_store = _session_store(resolved_config_path, account)
                _ensure_account_session(config, account, session_store)
                try:
                    with TronClassClient(config.http, session_store) as client:
                        response = client.get_rollcalls()
                        _process_rollcalls(
                            account,
                            response.rollcalls,
                            state_store=state_store,
                            notifier=notifier,
                            notify=notify,
                            sign_config=sign_config,
                            client=client,
                        )
                except TronClassError as exc:
                    if exc.status_code != 401 or not _try_relogin(config, account, session_store):
                        raise
                    with TronClassClient(config.http, session_store) as client:
                        response = client.get_rollcalls()
                        _process_rollcalls(
                            account,
                            response.rollcalls,
                            state_store=state_store,
                            notifier=notifier,
                            notify=notify,
                            sign_config=sign_config,
                            client=client,
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
    _print_watch_start(
        config,
        accounts_to_watch,
        all_accounts=all_accounts,
        account_id=account_id,
        interval_seconds=interval_seconds,
        sign_override=sign,
    )

    def on_tick(tick: int, new_count: int) -> None:
        console.print(f"tick={tick} new_rollcalls={new_count}")

    try:
        with state_store:
            if len(accounts_to_watch) == 1:
                account = accounts_to_watch[0]
                notifier = Notifier(_notify_config_for_account(config, account))
                sign_config = _sign_config_for_account(config, account, sign)
                session_store = _session_store(resolved_config_path, account)
                _ensure_account_session(config, account, session_store)
                with TronClassClient(config.http, session_store) as client:
                    def recover_session() -> bool:
                        if not _try_relogin(config, account, session_store):
                            return False
                        client.reload_session()
                        return True

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
                        recover_session=recover_session,
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
                        notifier = Notifier(_notify_config_for_account(config, account))
                        sign_config = _sign_config_for_account(config, account, sign)
                        session_store = _session_store(resolved_config_path, account)
                        _ensure_account_session(config, account, session_store)
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
                                if error.status_code == 401 and _try_relogin(config, account, session_store):
                                    with TronClassClient(config.http, session_store) as retry_client:
                                        try:
                                            total_new += len(
                                                poll_once(
                                                    account.id,
                                                    account.name,
                                                    retry_client,
                                                    state_store,
                                                    notifier,
                                                    sign_config,
                                                )
                                            )
                                        except TronClassError as retry_error:
                                            notify_error_once(
                                                account.id,
                                                account.name,
                                                state_store,
                                                notifier,
                                                retry_error,
                                                config.watch.alert_cooldown_seconds,
                                            )
                                else:
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
    account_id: Annotated[str, typer.Option("--account", "-a", help="Account ID.")] = "main",
    all_accounts: Annotated[bool, typer.Option("--all", help="Send test notifications to all enabled accounts.")] = False,
) -> None:
    config, _resolved_config_path = load_config(config_path)
    accounts_to_notify = config.enabled_accounts() if all_accounts else [_select_account(config, account_id)]
    for account in accounts_to_notify:
        notify_config = _notify_config_for_account(config, account)
        sent = Notifier(notify_config).send(
            NotificationMessage(
                title="USST rollcall test",
                body=f"Notification channel is working for account: {account.name}.",
            )
        )
        console.print(f"[{account.id}] Sent via: {', '.join(sent) if sent else '<none>'}")


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
