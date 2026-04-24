from __future__ import annotations

from urllib.parse import urljoin

import httpx

from .config import HttpConfig
from .models import RollcallResponse, SessionTokens
from .session import SessionStore


class TronClassError(RuntimeError):
    def __init__(self, message: str, *, status_code: int | None = None) -> None:
        super().__init__(message)
        self.status_code = status_code


class TronClassClient:
    def __init__(self, http_config: HttpConfig, session_store: SessionStore) -> None:
        self.http_config = http_config
        self.session_store = session_store
        self.tokens = session_store.load()
        self.client = httpx.Client(
            base_url=http_config.base_url,
            timeout=http_config.timeout_seconds,
            follow_redirects=False,
            headers=self._base_headers(),
            cookies=self.tokens.cookies,
        )

    def close(self) -> None:
        self.client.close()

    def __enter__(self) -> "TronClassClient":
        return self

    def __exit__(self, *_args: object) -> None:
        self.close()

    def _base_headers(self) -> dict[str, str]:
        headers = {
            "Accept": "application/json, text/plain, */*",
            "Accept-Language": "zh-Hans",
            "Origin": self.http_config.origin,
            "Referer": self.http_config.referer,
            "User-Agent": self.http_config.user_agent,
            "X-Requested-With": "XMLHttpRequest",
        }
        if self.tokens.x_session_id:
            headers["X-SESSION-ID"] = self.tokens.x_session_id
        return headers

    def _persist_response_session(self, response: httpx.Response) -> None:
        x_session_id = response.headers.get("X-SESSION-ID")
        cookies = dict(response.cookies)
        if not x_session_id and not cookies:
            return
        self.tokens = self.session_store.update(x_session_id=x_session_id, cookies=cookies)
        if self.tokens.x_session_id:
            self.client.headers["X-SESSION-ID"] = self.tokens.x_session_id
        for name, value in self.tokens.cookies.items():
            self.client.cookies.set(name, value)

    def get_rollcalls(self) -> RollcallResponse:
        response = self.client.get(
            "/api/radar/rollcalls",
            params={"api_version": self.http_config.api_version},
        )
        self._persist_response_session(response)
        if response.status_code >= 400:
            raise TronClassError(
                f"GET /api/radar/rollcalls failed: HTTP {response.status_code}",
                status_code=response.status_code,
            )
        try:
            return RollcallResponse.model_validate(response.json())
        except ValueError as exc:
            raise TronClassError("GET /api/radar/rollcalls did not return JSON") from exc

    def rollcall_url(self, rollcall_id: str) -> str:
        return urljoin(self.http_config.base_url, f"/api/rollcall/{rollcall_id}")

    def answer_rollcall(self, _rollcall_id: str) -> None:
        raise NotImplementedError("Submit endpoint is not implemented until a real sign-in capture is available.")
