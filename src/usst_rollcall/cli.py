from __future__ import annotations

from pathlib import Path
from typing import Annotated

import typer
from rich.console import Console
from rich.table import Table

from .client import TronClassClient, TronClassError
from .config import default_config_path, load_config, resolve_data_path, write_default_config
from .models import NotificationMessage
from .notify import Notifier
from .session import SessionStore, redact
from .state import StateStore
from .watcher import poll_once, watch


app = typer.Typer(help="USST TronClass rollcall watcher.")
console = Console()


def _load_runtime(config_path: Path | None) -> tuple:
    config, resolved_config_path = load_config(config_path)
    session_store = SessionStore(resolve_data_path(resolved_config_path, config.session_file))
    state_store = StateStore(resolve_data_path(resolved_config_path, config.state_file))
    return config, resolved_config_path, session_store, state_store


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


@app.command("session-set")
def session_set(
    x_session_id: Annotated[str, typer.Option(prompt=True, hide_input=True)],
    session_cookie: Annotated[str | None, typer.Option(help="Optional session cookie value.")] = None,
    config_path: Annotated[Path | None, typer.Option("--config", "-c")] = None,
) -> None:
    config, resolved_config_path = load_config(config_path)
    store = SessionStore(resolve_data_path(resolved_config_path, config.session_file))
    cookies = {"session": session_cookie} if session_cookie else None
    store.update(x_session_id=x_session_id, cookies=cookies)
    console.print("Session saved.")


@app.command("session-show")
def session_show(config_path: Annotated[Path | None, typer.Option("--config", "-c")] = None) -> None:
    config, resolved_config_path = load_config(config_path)
    store = SessionStore(resolve_data_path(resolved_config_path, config.session_file))
    tokens = store.load()
    table = Table("Field", "Value")
    table.add_row("config", str(resolved_config_path))
    table.add_row("session_file", str(store.path))
    table.add_row("x_session_id", redact(tokens.x_session_id))
    table.add_row("cookies", ", ".join(tokens.cookies.keys()) or "<empty>")
    table.add_row("updated_at", str(tokens.updated_at or "<never>"))
    console.print(table)


@app.command("poll-once")
def poll_once_command(
    config_path: Annotated[Path | None, typer.Option("--config", "-c")] = None,
    notify: Annotated[bool, typer.Option(help="Send notification for newly seen rollcalls.")] = False,
) -> None:
    config, _resolved_config_path, session_store, state_store = _load_runtime(config_path)
    notifier = Notifier(config.notify) if notify else None
    try:
        with state_store, TronClassClient(config.http, session_store) as client:
            response = client.get_rollcalls()
            console.print(f"Rollcalls: {len(response.rollcalls)}")
            for rollcall in response.rollcalls:
                is_new = state_store.upsert_seen(rollcall)
                if notify and is_new and notifier:
                    notifier.send(NotificationMessage(title="USST rollcall detected", body=rollcall.model_dump_json(indent=2)))
                    state_store.mark_notified(rollcall.key)
                console.print(f"- {rollcall.key} {rollcall.display_title} [{rollcall.type_label}] status={rollcall.status}")
    except TronClassError as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(1) from exc


@app.command("watch")
def watch_command(
    config_path: Annotated[Path | None, typer.Option("--config", "-c")] = None,
    interval: Annotated[float | None, typer.Option(help="Override interval seconds.")] = None,
    ticks: Annotated[int | None, typer.Option(help="Stop after N ticks, useful for testing.")] = None,
) -> None:
    config, _resolved_config_path, session_store, state_store = _load_runtime(config_path)
    interval_seconds = interval if interval is not None else config.watch.interval_seconds
    notifier = Notifier(config.notify)

    def on_tick(tick: int, new_count: int) -> None:
        console.print(f"tick={tick} new_rollcalls={new_count}")

    try:
        with state_store, TronClassClient(config.http, session_store) as client:
            watch(
                client,
                state_store,
                notifier,
                interval_seconds=interval_seconds,
                stop_after=ticks,
                on_tick=on_tick,
            )
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
