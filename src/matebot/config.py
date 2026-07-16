"""Configuration: TOML file overridden by environment variables."""

from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass, field, fields
from pathlib import Path

DEFAULT_CONFIG = Path(
    os.environ.get("XDG_CONFIG_HOME", os.path.expanduser("~/.config"))
) / "matebot" / "config.toml"

# dataclass field -> environment variable
ENV_MAP = {
    "machine_host": "MATEBOT_MACHINE_HOST",
    "messenger": "MATEBOT_MESSENGER",
    "telegram_token": "TELEGRAM_BOT_TOKEN",
    "telegram_chat_id": "TELEGRAM_CHAT_ID",
    "discord_token": "DISCORD_BOT_TOKEN",
    "discord_channel_id": "DISCORD_CHANNEL_ID",
    "data_repo": "MATEBOT_DATA_REPO",
    "state_dir": "MATEBOT_STATE_DIR",
    "min_shot_s": "MATEBOT_MIN_SHOT_S",
    "ignore_profiles": "MATEBOT_IGNORE_PROFILES",
    "sync_enabled": "MATEBOT_SYNC",
    "site_title": "MATEBOT_SITE_TITLE",
    "journal_url": "MATEBOT_JOURNAL_URL",
    "hints_enabled": "MATEBOT_HINTS",
    "digest_enabled": "MATEBOT_DIGEST",
    "clean_every": "MATEBOT_CLEAN_EVERY",
    "water_warn_pct": "MATEBOT_WATER_WARN",
    "autoheat_window": "MATEBOT_AUTOHEAT",
    "video_keep": "MATEBOT_VIDEO_KEEP",
    "camera_enabled": "MATEBOT_CAMERA",
    "camera_port": "MATEBOT_CAMERA_PORT",
    "camera_offset": "MATEBOT_CAMERA_OFFSET",
    "reel_enabled": "MATEBOT_REEL",
    "plots_enabled": "MATEBOT_PLOTS",
    "wake_hook": "MATEBOT_WAKE_HOOK",
    "sleep_hook": "MATEBOT_SLEEP_HOOK",
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
        + "/matebot"
    )
    min_shot_s: float = 10.0
    ignore_profiles: str = r"(?i)backflush|descale|flush|clean"
    sync_enabled: bool = False
    site_title: str = "Shot Journal"
    journal_url: str = ""  # public journal base URL, used for /last deep links
    hints_enabled: bool = True  # dial-in suggestions after sour/bitter/low-rated shots
    digest_enabled: bool = True  # weekly summary on Sunday evening
    clean_every: int = 40  # backflush reminder every N espresso shots; 0 = off
    water_warn_pct: int = 15  # warn when the tank drops below this percent; 0 = off
    # e.g. "06:30-07:00": machine powered on in this window switches to brew, once a day
    autoheat_window: str = ""
    video_keep: int = 15  # newest N shot videos kept in the journal; 0 = keep all
    camera_enabled: bool = False  # opt-in: serve the phone-camera page + record shots
    camera_port: int = 8877
    camera_offset: float = -1.0  # shot t=0 relative to video t=0 (detection+stream latency)
    reel_enabled: bool = True  # send a composed clip+chart reel after camera shots
    plots_enabled: bool = True  # send the shot chart as a photo (needs matplotlib)
    wake_hook: str = ""  # shell command to power the machine's smart plug ON
    sleep_hook: str = ""  # shell command to power the smart plug OFF

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
            if f.name in ("min_shot_s", "camera_offset"):
                value = float(value)
            elif f.name in ("clean_every", "water_warn_pct", "video_keep", "camera_port"):
                value = int(value)
            elif f.name in ("sync_enabled", "hints_enabled", "digest_enabled", "plots_enabled",
                            "camera_enabled", "reel_enabled"):
                value = str(value).lower() in ("1", "true", "yes", "on")
            kwargs[f.name] = value
        config = cls(**kwargs)
        if config.data_repo and "sync_enabled" not in kwargs:
            config.sync_enabled = True
        return config
