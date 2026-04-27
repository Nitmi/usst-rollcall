from __future__ import annotations

import os
from copy import deepcopy
from importlib import resources
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, Field


APP_NAME = "usst-rollcall"


def default_config_dir() -> Path:
    env_path = os.environ.get("USST_ROLLCALL_CONFIG_DIR")
    if env_path:
        return Path(env_path).expanduser()
    local_app_data = os.environ.get("LOCALAPPDATA")
    if local_app_data:
        return Path(local_app_data) / APP_NAME
    return Path.home() / ".config" / APP_NAME


def default_config_path() -> Path:
    return default_config_dir() / "config.yaml"


class HttpConfig(BaseModel):
    base_url: str = "https://1906.usst.edu.cn"
    origin: str = "http://10.1.15.15:28080"
    referer: str = "http://10.1.15.15:28080/"
    api_version: str = "1.1.0"
    timeout_seconds: float = 15.0
    user_agent: str = (
        "Mozilla/5.0 (iPhone; CPU iPhone OS 18_7 like Mac OS X) "
        "AppleWebKit/605.1.15 (KHTML, like Gecko) "
        "Mobile/15E148/HuaWei-AnyOffice/2.6.1802.0010/"
        "com.huawei.cloudlink.workplace"
    )


class WatchConfig(BaseModel):
    interval_seconds: float = 10.0
    alert_cooldown_seconds: float = 1800.0
    active_start: str = "07:30"
    active_end: str = "20:30"
    timezone: str = "Asia/Shanghai"


class LoginConfig(BaseModel):
    enabled: bool = False
    login_url: str = "https://1906.usst.edu.cn/login?next=/user/index"
    form_id: str = "casLoginForm"
    username: str = ""
    password: str = ""
    password_env: str | None = None
    captcha: str = ""
    user_agent: str = (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/136.0.0.0 Safari/537.36"
    )
    username_field: str = "username"
    password_field: str = "password"
    captcha_field: str = "captchaResponse"
    success_probe: str = "/api/profile"

    def resolved_password(self) -> str:
        if self.password_env:
            return os.environ.get(self.password_env, "")
        return self.password


class RadarLocationConfig(BaseModel):
    latitude: float | None = None
    longitude: float | None = None
    accuracy: float = 35.0
    altitude: float = 0.0
    altitude_accuracy: float | None = None
    heading: float | None = None
    speed: float | None = None


class SignConfig(BaseModel):
    enabled: bool = False
    number_enabled: bool = True
    radar_enabled: bool = False
    notify_result: bool = True
    device_id: str | None = None
    radar_location: RadarLocationConfig = Field(default_factory=RadarLocationConfig)


class BarkConfig(BaseModel):
    enabled: bool = False
    server: str = "https://api.day.app"
    key: str = ""
    sound: str | None = None
    group: str = "USST Rollcall"


class GotifyConfig(BaseModel):
    enabled: bool = False
    server: str = ""
    token: str = ""
    priority: int = 5


class EmailConfig(BaseModel):
    enabled: bool = False
    smtp_host: str = ""
    smtp_port: int = 587
    username: str = ""
    password: str = ""
    from_addr: str = ""
    to_addrs: list[str] = Field(default_factory=list)
    use_tls: bool = True


class ConsoleConfig(BaseModel):
    enabled: bool = True


class NotifyConfig(BaseModel):
    console: ConsoleConfig = Field(default_factory=ConsoleConfig)
    bark: BarkConfig = Field(default_factory=BarkConfig)
    gotify: GotifyConfig = Field(default_factory=GotifyConfig)
    email: EmailConfig = Field(default_factory=EmailConfig)


class AccountConfig(BaseModel):
    id: str = "main"
    name: str = "Main"
    enabled: bool = True
    session_file: str = "sessions/main.json"
    login: LoginConfig = Field(default_factory=LoginConfig)
    notify: NotifyConfig = Field(default_factory=NotifyConfig)
    sign: SignConfig = Field(default_factory=SignConfig)


class AppConfig(BaseModel):
    http: HttpConfig = Field(default_factory=HttpConfig)
    watch: WatchConfig = Field(default_factory=WatchConfig)
    accounts: list[AccountConfig] = Field(default_factory=lambda: [AccountConfig()])

    state_file: str = "state.sqlite3"

    def get_account(self, account_id: str) -> AccountConfig:
        for account in self.accounts:
            if account.id == account_id:
                return account
        raise KeyError(f"Unknown account: {account_id}")

    def enabled_accounts(self) -> list[AccountConfig]:
        return [account for account in self.accounts if account.enabled]


def deep_merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(base.get(key), dict):
            base[key] = deep_merge(base[key], value)
        else:
            base[key] = value
    return base


def _merge_account_section(default_section: Any, account_section: Any) -> dict[str, Any]:
    base = deepcopy(default_section) if isinstance(default_section, dict) else {}
    if isinstance(account_section, dict):
        return deep_merge(base, deepcopy(account_section))
    return base


def _normalize_config_data(raw_data: Any) -> dict[str, Any]:
    data = deepcopy(raw_data) if isinstance(raw_data, dict) else {}
    default_login = data.pop("login", None)
    default_notify = data.pop("notify", None)
    default_sign = data.pop("sign", None)

    raw_accounts = data.get("accounts")
    accounts = raw_accounts if isinstance(raw_accounts, list) and raw_accounts else [{}]
    normalized_accounts: list[dict[str, Any]] = []
    for raw_account in accounts:
        account = deepcopy(raw_account) if isinstance(raw_account, dict) else {}
        account["login"] = _merge_account_section(default_login, account.get("login"))
        account["notify"] = _merge_account_section(default_notify, account.get("notify"))
        account["sign"] = _merge_account_section(default_sign, account.get("sign"))
        normalized_accounts.append(account)
    data["accounts"] = normalized_accounts
    return data


def _default_config_template() -> str:
    repo_template = Path(__file__).resolve().parents[2] / "examples" / "config.yaml"
    if repo_template.exists():
        return repo_template.read_text(encoding="utf-8")
    return resources.files("usst_rollcall").joinpath("default_config.yaml").read_text(encoding="utf-8")


def load_config(path: Path | None = None) -> tuple[AppConfig, Path]:
    config_path = path or default_config_path()
    if not config_path.exists():
        return AppConfig(), config_path
    data = _normalize_config_data(yaml.safe_load(config_path.read_text(encoding="utf-8")) or {})
    return AppConfig.model_validate(data), config_path


def write_default_config(path: Path | None = None, *, force: bool = False) -> Path:
    config_path = path or default_config_path()
    if config_path.exists() and not force:
        raise FileExistsError(f"Config already exists: {config_path}")
    config_path.parent.mkdir(parents=True, exist_ok=True)
    config_path.write_text(_default_config_template(), encoding="utf-8")
    return config_path


def resolve_data_path(config_path: Path, value: str) -> Path:
    path = Path(value).expanduser()
    if path.is_absolute():
        return path
    return config_path.parent / path
