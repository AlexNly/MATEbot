"""Configuration: TOML file overridden by environment variables."""

from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass, field, fields
from pathlib import Path

DEFAULT_CONFIG = Path(
    os.environ.get("XDG_CONFIG_HOME", os.path.expanduser("~/.config"))
) / "gaggibot" / "config.toml"

# dataclass field -> environment variable
ENV_MAP = {
    "machine_host": "GAGGIBOT_MACHINE_HOST",
    "messenger": "GAGGIBOT_MESSENGER",
    "telegram_token": "TELEGRAM_BOT_TOKEN",
    "telegram_chat_id": "TELEGRAM_CHAT_ID",
    "discord_token": "DISCORD_BOT_TOKEN",
    "discord_channel_id": "DISCORD_CHANNEL_ID",
    "data_repo": "GAGGIBOT_DATA_REPO",
    "state_dir": "GAGGIBOT_STATE_DIR",
    "min_shot_s": "GAGGIBOT_MIN_SHOT_S",
    "ignore_profiles": "GAGGIBOT_IGNORE_PROFILES",
    "sync_enabled": "GAGGIBOT_SYNC",
    "site_title": "GAGGIBOT_SITE_TITLE",
    "journal_url": "GAGGIBOT_JOURNAL_URL",
    "hints_enabled": "GAGGIBOT_HINTS",
    "digest_enabled": "GAGGIBOT_DIGEST",
}


@dataclass
class Config:
    machine_host: str = "gaggimate.local"
    messenger: str = "telegram"
    telegram_token: str = ""
    telegram_chat_id: str = ""
    discord_token: str = ""
    discord_channel_id: str = ""
    data_repo: str = ""  # path to the git-backed shot journal; empty = sync off
    state_dir: str = field(
        default_factory=lambda: os.environ.get(
            "XDG_STATE_HOME", os.path.expanduser("~/.local/state")
        )
        + "/gaggibot"
    )
    min_shot_s: float = 10.0
    ignore_profiles: str = r"(?i)backflush|descale|flush|clean"
    sync_enabled: bool = False
    site_title: str = "Shot Journal"
    journal_url: str = ""  # public journal base URL, used for /last deep links
    hints_enabled: bool = True  # dial-in suggestions after sour/bitter/low-rated shots
    digest_enabled: bool = True  # weekly summary on Sunday evening

    @classmethod
    def load(cls, path: str | Path | None = None) -> Config:
        raw: dict = {}
        cfg_path = Path(path) if path else DEFAULT_CONFIG
        if cfg_path.exists():
            raw = tomllib.loads(cfg_path.read_text())
        kwargs = {}
        for f in fields(cls):
            value = raw.get(f.name)
            env = os.environ.get(ENV_MAP.get(f.name, ""))
            if env is not None:
                value = env
            if value is None:
                continue
            if f.name == "min_shot_s":
                value = float(value)
            elif f.name in ("sync_enabled", "hints_enabled", "digest_enabled"):
                value = str(value).lower() in ("1", "true", "yes", "on")
            kwargs[f.name] = value
        config = cls(**kwargs)
        if config.data_repo and "sync_enabled" not in kwargs:
            config.sync_enabled = True
        return config
