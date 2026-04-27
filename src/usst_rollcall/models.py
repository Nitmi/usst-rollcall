from __future__ import annotations

import json
from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class Rollcall(BaseModel):
    """Flexible model for TronClass rollcall payloads."""

    model_config = ConfigDict(extra="allow")

    rollcall_id: str | int | None = None
    id: str | int | None = None
    course_title: str | None = None
    title: str | None = None
    created_by_name: str | None = None
    department_name: str | None = None
    is_expired: bool | None = None
    is_number: bool | None = None
    is_radar: bool | None = None
    rollcall_status: str | None = None
    status: str | None = None
    scored: bool | None = None

    @property
    def key(self) -> str:
        value = self.rollcall_id if self.rollcall_id is not None else self.id
        if value is not None:
            return str(value)
        payload = json.dumps(self.model_dump(mode="json"), ensure_ascii=False, sort_keys=True)
        return str(abs(hash(payload)))

    @property
    def display_title(self) -> str:
        return self.course_title or self.title or "Unknown course"

    @property
    def type_label(self) -> str:
        if self.is_radar:
            return "radar"
        if self.is_number:
            return "number"
        return "unknown"


class RollcallResponse(BaseModel):
    model_config = ConfigDict(extra="allow")

    rollcalls: list[Rollcall] = Field(default_factory=list)


class SessionTokens(BaseModel):
    x_session_id: str | None = None
    cookies: dict[str, str] = Field(default_factory=dict)
    updated_at: datetime | None = None

    def is_empty(self) -> bool:
        return not self.x_session_id and not self.cookies


class NotificationMessage(BaseModel):
    title: str
    body: str
    url: str | None = None
    data: dict[str, Any] = Field(default_factory=dict)


class SignResult(BaseModel):
    attempted: bool
    success: bool
    method: str
    message: str
    rollcall_id: str | None = None
    raw: Any | None = None


class LoginResult(BaseModel):
    success: bool
    message: str
    final_url: str | None = None
    profile_id: str | None = None
    profile_name: str | None = None
