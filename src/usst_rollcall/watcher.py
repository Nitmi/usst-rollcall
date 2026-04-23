from __future__ import annotations

import time
from collections.abc import Callable

from .client import TronClassClient
from .models import NotificationMessage, Rollcall
from .notify import Notifier
from .state import StateStore


def build_rollcall_message(rollcall: Rollcall) -> NotificationMessage:
    body = "\n".join(
        [
            f"Course: {rollcall.display_title}",
            f"Type: {rollcall.type_label}",
            f"Status: {rollcall.status or 'unknown'}",
            f"Rollcall ID: {rollcall.key}",
        ]
    )
    return NotificationMessage(title="USST rollcall detected", body=body)


def poll_once(client: TronClassClient, state: StateStore, notifier: Notifier | None = None) -> list[Rollcall]:
    response = client.get_rollcalls()
    new_rollcalls: list[Rollcall] = []
    for rollcall in response.rollcalls:
        is_new = state.upsert_seen(rollcall)
        if is_new:
            new_rollcalls.append(rollcall)
            if notifier:
                notifier.send(build_rollcall_message(rollcall))
                state.mark_notified(rollcall.key)
    return new_rollcalls


def watch(
    client: TronClassClient,
    state: StateStore,
    notifier: Notifier,
    *,
    interval_seconds: float,
    stop_after: int | None = None,
    on_tick: Callable[[int, int], None] | None = None,
) -> None:
    tick = 0
    while True:
        tick += 1
        new_rollcalls = poll_once(client, state, notifier)
        if on_tick:
            on_tick(tick, len(new_rollcalls))
        if stop_after is not None and tick >= stop_after:
            return
        time.sleep(interval_seconds)
