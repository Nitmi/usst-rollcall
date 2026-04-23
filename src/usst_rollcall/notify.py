from __future__ import annotations

import smtplib
from email.message import EmailMessage
from urllib.parse import quote

import httpx
from rich.console import Console

from .config import NotifyConfig
from .models import NotificationMessage


class NotificationError(RuntimeError):
    pass


class Notifier:
    def __init__(self, config: NotifyConfig) -> None:
        self.config = config
        self.console = Console()

    def send(self, message: NotificationMessage) -> list[str]:
        sent: list[str] = []
        errors: list[str] = []
        if self.config.console.enabled:
            self._send_console(message)
            sent.append("console")
        if self.config.bark.enabled:
            self._send_bark(message)
            sent.append("bark")
        if self.config.gotify.enabled:
            self._send_gotify(message)
            sent.append("gotify")
        if self.config.email.enabled:
            self._send_email(message)
            sent.append("email")
        if not sent and errors:
            raise NotificationError("; ".join(errors))
        return sent

    def _send_console(self, message: NotificationMessage) -> None:
        self.console.print(f"[bold yellow]{message.title}[/bold yellow]")
        self.console.print(message.body)
        if message.url:
            self.console.print(message.url)

    def _send_bark(self, message: NotificationMessage) -> None:
        cfg = self.config.bark
        if not cfg.key:
            raise NotificationError("Bark is enabled but key is empty")
        title = quote(message.title, safe="")
        body = quote(message.body, safe="")
        url = f"{cfg.server.rstrip('/')}/{cfg.key}/{title}/{body}"
        params: dict[str, str] = {"group": cfg.group}
        if cfg.sound:
            params["sound"] = cfg.sound
        if message.url:
            params["url"] = message.url
        response = httpx.get(url, params=params, timeout=10)
        response.raise_for_status()

    def _send_gotify(self, message: NotificationMessage) -> None:
        cfg = self.config.gotify
        if not cfg.server or not cfg.token:
            raise NotificationError("Gotify is enabled but server/token is empty")
        response = httpx.post(
            f"{cfg.server.rstrip('/')}/message",
            params={"token": cfg.token},
            json={
                "title": message.title,
                "message": message.body,
                "priority": cfg.priority,
                "extras": {"client::display": {"contentType": "text/markdown"}},
            },
            timeout=10,
        )
        response.raise_for_status()

    def _send_email(self, message: NotificationMessage) -> None:
        cfg = self.config.email
        if not cfg.smtp_host or not cfg.from_addr or not cfg.to_addrs:
            raise NotificationError("Email is enabled but smtp_host/from_addr/to_addrs is incomplete")
        email = EmailMessage()
        email["Subject"] = message.title
        email["From"] = cfg.from_addr
        email["To"] = ", ".join(cfg.to_addrs)
        body = message.body
        if message.url:
            body = f"{body}\n\n{message.url}"
        email.set_content(body)
        with smtplib.SMTP(cfg.smtp_host, cfg.smtp_port, timeout=15) as smtp:
            if cfg.use_tls:
                smtp.starttls()
            if cfg.username:
                smtp.login(cfg.username, cfg.password)
            smtp.send_message(email)
