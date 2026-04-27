from __future__ import annotations

import html
import re
from html.parser import HTMLParser
from urllib.parse import urljoin

import httpx

from .config import HttpConfig, LoginConfig
from .models import LoginResult, SessionTokens
from .session import SessionStore


class LoginError(RuntimeError):
    pass


class LoginFormParser(HTMLParser):
    def __init__(self, form_id: str) -> None:
        super().__init__()
        self.target_form_id = form_id
        self.in_form = False
        self.form_action: str | None = None
        self.form_method = "post"
        self.inputs: dict[str, str] = {}

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attrs_dict = dict(attrs)
        if tag == "form":
            if attrs_dict.get("id") == self.target_form_id:
                self.in_form = True
                self.form_action = attrs_dict.get("action")
                self.form_method = (attrs_dict.get("method") or "post").lower()
        elif tag == "input" and self.in_form:
            name = attrs_dict.get("name")
            if name:
                self.inputs[name] = attrs_dict.get("value") or ""

    def handle_endtag(self, tag: str) -> None:
        if tag == "form" and self.in_form:
            self.in_form = False


def _find_first(pattern: str, text: str) -> str | None:
    match = re.search(pattern, text, re.IGNORECASE | re.DOTALL)
    if not match:
        return None
    return html.unescape(match.group(1).strip())


def _response_message(body: str) -> str:
    message = _find_first(r'id="msg"[^>]*>\s*(.*?)\s*<', body)
    if message:
        return message
    title = _find_first(r"<title>\s*(.*?)\s*</title>", body)
    if title:
        return title
    return "login failed"


def _login_headers(http_config: HttpConfig, login_config: LoginConfig) -> dict[str, str]:
    return {
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "zh-Hans",
        "User-Agent": login_config.user_agent or http_config.user_agent,
    }


def _probe_headers(http_config: HttpConfig) -> dict[str, str]:
    return {
        "Accept": "application/json, text/plain, */*",
        "Accept-Language": "zh-Hans",
        "Origin": http_config.origin,
        "Referer": http_config.referer,
        "User-Agent": http_config.user_agent,
        "X-Requested-With": "XMLHttpRequest",
    }


def _looks_like_login_page(response: httpx.Response) -> bool:
    path = response.url.path.lower()
    if "/authserver/login" in path:
        return True
    content_type = (response.headers.get("content-type") or "").lower()
    if "text/html" not in content_type:
        return False
    body = response.text
    return 'id="casLoginForm"' in body or "统一身份认证" in body


def _need_captcha(client: httpx.Client, login_page: httpx.Response, login_config: LoginConfig) -> bool:
    username = (login_config.username or "").strip()
    if not username:
        return False
    endpoint = urljoin(str(login_page.url), f"needCaptcha.html?username={username}")
    response = client.get(
        endpoint,
        headers={
            "Accept": "application/json, text/javascript, */*; q=0.01",
            "Accept-Language": "zh-Hans",
            "Referer": str(login_page.url),
            "User-Agent": login_config.user_agent,
            "X-Requested-With": "XMLHttpRequest",
        },
    )
    response.raise_for_status()
    return response.text.strip().lower() == "true"


def _cookie_value(client: httpx.Client, name: str, *, domain_suffix: str | None = None) -> str | None:
    for cookie in client.cookies.jar:
        if cookie.name != name:
            continue
        if domain_suffix and not cookie.domain.endswith(domain_suffix):
            continue
        return cookie.value
    return None


def _persistable_cookies(client: httpx.Client) -> dict[str, str]:
    cookies: dict[str, str] = {}
    for cookie in client.cookies.jar:
        if cookie.domain.endswith("1906.usst.edu.cn") or cookie.domain == ".usst.edu.cn":
            cookies[cookie.name] = cookie.value
    return cookies


def login(http_config: HttpConfig, login_config: LoginConfig, session_store: SessionStore) -> LoginResult:
    if not login_config.enabled:
        raise LoginError("login is disabled in config")
    if not login_config.username:
        raise LoginError("login username is empty")
    password = login_config.resolved_password()
    if not password:
        raise LoginError("login password is empty")

    with httpx.Client(
        timeout=http_config.timeout_seconds,
        follow_redirects=True,
        headers=_login_headers(http_config, login_config),
    ) as client:
        login_page = client.get(login_config.login_url)
        login_page.raise_for_status()
        parser = LoginFormParser(login_config.form_id)
        parser.feed(login_page.text)
        if not parser.form_action:
            raise LoginError(f"unable to find form#{login_config.form_id}")
        if _need_captcha(client, login_page, login_config) and not login_config.captcha:
            raise LoginError("login requires captcha, but login.captcha is empty")

        form_data = dict(parser.inputs)
        form_data[login_config.username_field] = login_config.username
        form_data[login_config.password_field] = password
        if login_config.captcha:
            form_data[login_config.captcha_field] = login_config.captcha

        form_action = urljoin(str(login_page.url), parser.form_action)
        response = client.post(
            form_action,
            data=form_data,
            headers={
                **_login_headers(http_config, login_config),
                "Content-Type": "application/x-www-form-urlencoded",
                "Referer": str(login_page.url),
            },
        )
        if response.status_code >= 400:
            raise LoginError(f"login form POST failed: HTTP {response.status_code}")
        if _looks_like_login_page(response):
            message = _response_message(response.text)
            raise LoginError(f"login returned to CAS page: {message}")

        probe = client.get(
            urljoin(http_config.base_url, login_config.success_probe),
            follow_redirects=False,
            headers=_probe_headers(http_config),
        )
        if probe.status_code >= 400:
            raise LoginError(f"login probe failed: GET {login_config.success_probe} returned HTTP {probe.status_code}")
        if _looks_like_login_page(probe):
            raise LoginError("login probe redirected back to CAS login page")

        try:
            profile = probe.json() if probe.content else None
        except ValueError as exc:
            raise LoginError(f"login probe did not return JSON: {probe.text[:120]!r}") from exc

        profile_id = profile.get("id") if isinstance(profile, dict) else None
        profile_name = None
        if isinstance(profile, dict):
            profile_name = profile.get("name") or profile.get("nickname")

        if profile_id is None:
            raise LoginError("login probe returned no profile id")

        x_session_id = probe.headers.get("X-SESSION-ID")
        cookies = _persistable_cookies(client)
        if not x_session_id:
            x_session_id = cookies.get("session") or _cookie_value(client, "session", domain_suffix="1906.usst.edu.cn")
        tokens = SessionTokens(x_session_id=x_session_id, cookies=cookies)
        session_store.save(tokens)

        return LoginResult(
            success=True,
            message="login succeeded",
            final_url=str(response.url),
            profile_id=str(profile_id),
            profile_name=str(profile_name) if profile_name is not None else None,
        )
