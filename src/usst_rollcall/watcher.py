from __future__ import annotations

import time
from collections.abc import Callable
from datetime import datetime, time as datetime_time

from .client import TronClassClient, TronClassError
from .config import SignConfig
from .models import NotificationMessage, Rollcall, SignResult
from .notify import Notifier
from .signer import attempt_sign
from .state import StateStore


def parse_clock(value: str) -> datetime_time:
    return datetime.strptime(value, "%H:%M").time()


def is_within_active_window(now: datetime, start: datetime_time, end: datetime_time) -> bool:
    current = now.time()
    if start <= end:
        return start <= current <= end
    return current >= start or current <= end


def build_rollcall_message(account_name: str, rollcall: Rollcall) -> NotificationMessage:
    body = "\n".join(
        [
            f"Account: {account_name}",
            f"Course: {rollcall.display_title}",
            f"Type: {rollcall.type_label}",
            f"Status: {rollcall.status or 'unknown'}",
            f"Rollcall ID: {rollcall.key}",
        ]
    )
    return NotificationMessage(title="USST rollcall detected", body=body)


def build_error_message(account_name: str, error: TronClassError) -> NotificationMessage:
    status = error.status_code or "unknown"
    hint = "Refresh X-SESSION-ID/session cookie and run session-set." if error.status_code == 401 else "Check network, session, and API availability."
    body = "\n".join(
        [
            f"Account: {account_name}",
            f"Status: {status}",
            f"Error: {error}",
            f"Action: {hint}",
        ]
    )
    return NotificationMessage(title="USST rollcall watcher error", body=body)


def build_sign_message(account_name: str, rollcall: Rollcall, result: SignResult) -> NotificationMessage:
    status = "success" if result.success else "failed" if result.attempted else "skipped"
    body = "\n".join(
        [
            f"Account: {account_name}",
            f"Course: {rollcall.display_title}",
            f"Type: {rollcall.type_label}",
            f"Rollcall ID: {rollcall.key}",
            f"Sign status: {status}",
            f"Method: {result.method}",
            f"Message: {result.message}",
        ]
    )
    return NotificationMessage(title="USST rollcall sign result", body=body)


def notify_error_once(
    account_id: str,
    account_name: str,
    state: StateStore,
    notifier: Notifier,
    error: TronClassError,
    cooldown_seconds: float,
) -> bool:
    alert_key = f"poll_error:{error.status_code or 'unknown'}"
    if not state.should_send_alert(account_id, alert_key, cooldown_seconds):
        return False
    notifier.send(build_error_message(account_name, error))
    state.mark_alert_sent(account_id, alert_key)
    return True


def poll_once(
    account_id: str,
    account_name: str,
    client: TronClassClient,
    state: StateStore,
    notifier: Notifier | None = None,
    sign_config: SignConfig | None = None,
) -> list[Rollcall]:
    response = client.get_rollcalls()
    new_rollcalls: list[Rollcall] = []
    for rollcall in response.rollcalls:
        is_new = state.upsert_seen(account_id, rollcall)
        if is_new:
            new_rollcalls.append(rollcall)
            if notifier:
                notifier.send(build_rollcall_message(account_name, rollcall))
                state.mark_notified(account_id, rollcall.key)
        if sign_config and sign_config.enabled and not state.has_sign_result(account_id, rollcall.key):
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
            state.mark_sign_result(account_id, rollcall.key, result)
            if notifier and sign_config.notify_result:
                notifier.send(build_sign_message(account_name, rollcall, result))
    return new_rollcalls


def watch(
    account_id: str,
    account_name: str,
    client: TronClassClient,
    state: StateStore,
    notifier: Notifier,
    *,
    interval_seconds: float,
    alert_cooldown_seconds: float,
    active_start: str,
    active_end: str,
    sign_config: SignConfig | None = None,
    stop_after: int | None = None,
    on_tick: Callable[[int, int], None] | None = None,
) -> None:
    tick = 0
    start_time = parse_clock(active_start)
    end_time = parse_clock(active_end)
    while True:
        tick += 1
        if not is_within_active_window(datetime.now().astimezone(), start_time, end_time):
            if on_tick:
                on_tick(tick, 0)
            if stop_after is not None and tick >= stop_after:
                return
            time.sleep(interval_seconds)
            continue
        try:
            new_rollcalls = poll_once(account_id, account_name, client, state, notifier, sign_config)
        except TronClassError as error:
            notify_error_once(account_id, account_name, state, notifier, error, alert_cooldown_seconds)
            new_rollcalls = []
        if on_tick:
            on_tick(tick, len(new_rollcalls))
        if stop_after is not None and tick >= stop_after:
            return
        time.sleep(interval_seconds)
