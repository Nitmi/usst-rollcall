from __future__ import annotations

import uuid
from typing import Any

from .client import TronClassClient, TronClassError
from .config import SignConfig
from .models import Rollcall, SignResult


COMPLETED_STATUSES = {"on_call_fine", "attended", "present", "signed", "submitted"}


def find_number_code(payload: Any) -> str | None:
    if isinstance(payload, dict):
        for key in ("number_code", "numberCode", "number"):
            value = payload.get(key)
            if value is not None and str(value).strip():
                return str(value).strip()
        for value in payload.values():
            found = find_number_code(value)
            if found:
                return found
    if isinstance(payload, list):
        for item in payload:
            found = find_number_code(item)
            if found:
                return found
    return None


def should_skip_rollcall(rollcall: Rollcall) -> str | None:
    if rollcall.is_expired:
        return "rollcall is expired"
    status = (rollcall.status or rollcall.rollcall_status or "").strip().lower()
    if status in COMPLETED_STATUSES:
        return f"rollcall is already completed: {status}"
    if status and status != "absent":
        return f"rollcall status is not signable: {status}"
    return None


def resolve_device_id(config: SignConfig) -> str:
    return config.device_id or uuid.uuid4().hex


def build_radar_payload(config: SignConfig) -> dict[str, Any]:
    location = config.radar_location
    return {
        "accuracy": location.accuracy,
        "altitude": location.altitude,
        "altitudeAccuracy": location.altitude_accuracy,
        "deviceId": resolve_device_id(config),
        "heading": location.heading,
        "latitude": location.latitude,
        "longitude": location.longitude,
        "speed": location.speed,
    }


def attempt_sign(client: TronClassClient, rollcall: Rollcall, config: SignConfig) -> SignResult:
    rollcall_id = rollcall.key
    if not config.enabled:
        return SignResult(
            attempted=False,
            success=False,
            method="disabled",
            message="auto sign is disabled",
            rollcall_id=rollcall_id,
        )

    skip_reason = should_skip_rollcall(rollcall)
    if skip_reason:
        return SignResult(
            attempted=False,
            success=False,
            method=rollcall.type_label,
            message=skip_reason,
            rollcall_id=rollcall_id,
        )

    try:
        if rollcall.is_number:
            return attempt_number_sign(client, rollcall_id, config)
        if rollcall.is_radar:
            return attempt_radar_sign(client, rollcall_id, config)
    except TronClassError:
        raise
    except Exception as exc:
        return SignResult(
            attempted=True,
            success=False,
            method=rollcall.type_label,
            message=f"sign failed before submit: {exc}",
            rollcall_id=rollcall_id,
        )

    return SignResult(
        attempted=False,
        success=False,
        method=rollcall.type_label,
        message="unsupported rollcall type",
        rollcall_id=rollcall_id,
    )


def attempt_number_sign(client: TronClassClient, rollcall_id: str, config: SignConfig) -> SignResult:
    if not config.number_enabled:
        return SignResult(
            attempted=False,
            success=False,
            method="number",
            message="number sign is disabled",
            rollcall_id=rollcall_id,
        )
    detail = client.get_student_rollcalls(rollcall_id)
    number_code = find_number_code(detail)
    if not number_code:
        return SignResult(
            attempted=False,
            success=False,
            method="number",
            message="number code was not found in student_rollcalls response",
            rollcall_id=rollcall_id,
            raw=detail,
        )
    raw = client.answer_number_rollcall(rollcall_id, number_code, resolve_device_id(config))
    return SignResult(
        attempted=True,
        success=True,
        method="number",
        message="number rollcall submitted",
        rollcall_id=rollcall_id,
        raw=raw,
    )


def attempt_radar_sign(client: TronClassClient, rollcall_id: str, config: SignConfig) -> SignResult:
    if not config.radar_enabled:
        return SignResult(
            attempted=False,
            success=False,
            method="radar",
            message="radar sign is disabled",
            rollcall_id=rollcall_id,
        )
    if config.radar_location.latitude is None or config.radar_location.longitude is None:
        return SignResult(
            attempted=False,
            success=False,
            method="radar",
            message="radar latitude/longitude are not configured",
            rollcall_id=rollcall_id,
        )
    raw = client.answer_radar_rollcall(rollcall_id, build_radar_payload(config))
    return SignResult(
        attempted=True,
        success=True,
        method="radar",
        message="radar rollcall submitted",
        rollcall_id=rollcall_id,
        raw=raw,
    )
