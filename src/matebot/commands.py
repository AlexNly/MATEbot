"""Chat commands — messenger-agnostic.

Commands arrive as plain text events starting with "/" (every backend already
delivers text), so this router works identically on Telegram, Discord and any
future messenger. It is consulted before the questionnaire so a command never
gets swallowed as a free-text answer.
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Any

from .machine import MachineError

log = logging.getLogger(__name__)

MODE_NAMES = {0: "standby", 1: "brew", 2: "steam", 3: "water", 4: "grind"}

HELP = (
    "/wake — turn the machine on (brew mode); I'll ping you when it's at temperature\n"
    "/sleep — back to standby\n"
    "/status — mode, temperature, connectivity\n"
    "/last — the last logged shot\n"
    "/fix — redo the questionnaire for the last shot\n"
    "/newbag <grams> [name] — start tracking a bean bag (optional feature)\n"
    "/bag — how much is left in the open bags\n"
    "/tossbag [name] — close out a bag (emptied, binned, or gifted)\n"
    "/digest — your last 7 days in espresso\n"
    "/help — this list"
)


class CommandRouter:
    def __init__(self, client, state, convo, messenger, config, latest_frame) -> None:
        self.client = client
        self.state = state
        self.convo = convo
        self.messenger = messenger
        self.config = config
        self.latest_frame = latest_frame  # () -> (frame dict | None, age seconds)
        self._awaiting_ready = False
        self._args: list[str] = []

    # ------------------------------------------------------------- dispatch

    async def handle(self, text: str) -> bool:
        """Handle a command; returns True when the text was consumed."""
        parts = text.strip().split()
        cmd = parts[0].lower().lstrip("/").split("@")[0]
        self._args = parts[1:]
        handler = getattr(self, f"_cmd_{cmd}", None)
        if handler is None:
            if cmd == "start":
                await self.messenger.send("☕ matebot at your service.\n\n" + HELP)
                return True
            return False
        try:
            await handler()
        except MachineError as exc:
            await self.messenger.send(f"⚠️ Can't reach the machine ({exc}). Is it plugged in?")
        except Exception:  # noqa: BLE001
            log.exception("command %s failed", cmd)
            await self.messenger.send("⚠️ That didn't work — check the logs.")
        return True

    # ------------------------------------------------------------- commands

    async def _cmd_help(self) -> None:
        await self.messenger.send(HELP)

    async def _run_hook(self, hook: str, label: str) -> bool:
        """Run a smart-plug shell hook; reports failure to the user."""
        try:
            proc = await asyncio.create_subprocess_shell(
                hook,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
            )
            out, _ = await asyncio.wait_for(proc.communicate(), timeout=20)
            if proc.returncode != 0:
                log.warning("%s hook failed (%d): %s", label, proc.returncode, out.decode()[-200:])
                await self.messenger.send(f"⚠️ The {label} hook failed — check the plug.")
                return False
            return True
        except Exception as exc:  # noqa: BLE001
            log.warning("%s hook error: %s", label, exc)
            await self.messenger.send(f"⚠️ The {label} hook errored: {exc}")
            return False

    async def _machine_online(self) -> bool:
        _, age = self.latest_frame()
        return age < 20

    async def _cmd_wake(self) -> None:
        cold_start = self.config.wake_hook and not await self._machine_online()
        if self.config.wake_hook and not await self._run_hook(self.config.wake_hook, "wake"):
            return
        if cold_start:
            await self.messenger.send(
                "🔌 Plug is on — waiting for the machine to boot (can take a minute)…"
            )
            for _ in range(90):
                await asyncio.sleep(1)
                if await self._machine_online():
                    break
            else:
                await self.messenger.send(
                    "⚠️ The machine didn't come online. Check the plug and the machine's switch."
                )
                return
        await self.client.send_event("req:change-mode", mode=1)
        self._awaiting_ready = True
        await self.messenger.send("🔥 Waking the machine — I'll tell you when it's hot.")

    async def _cmd_sleep(self) -> None:
        try:
            await self.client.send_event("req:change-mode", mode=0)
        except MachineError:
            if not self.config.sleep_hook:
                raise
        self._awaiting_ready = False
        if self.config.sleep_hook:
            await asyncio.sleep(3)  # let the machine settle into standby first
            if await self._run_hook(self.config.sleep_hook, "sleep"):
                await self.messenger.send("😴 Standby, and the plug is off. Fully dark.")
                return
        await self.messenger.send("😴 Machine is going to standby.")

    async def _cmd_status(self) -> None:
        frame, age = self.latest_frame()
        if frame is None or age > 20:
            await self.messenger.send(
                "🔌 Machine is offline (no status for a while) — probably powered off."
            )
            return
        mode = MODE_NAMES.get(frame.get("m"), "?")
        ct, tt = frame.get("ct", 0), frame.get("tt", 0)
        line = f"Machine is in {mode} mode · boiler {ct:.1f}°C"
        if tt:
            line += f" → target {tt:.0f}°C"
        if frame.get("wl") is not None:
            line += f" · water {frame['wl']}%"
        since_clean = self.state.get("shots_since_clean", 0)
        if since_clean:
            line += f"\n🧽 {since_clean} shots since the last backflush"
        await self.messenger.send(line)

    async def _cmd_last(self) -> None:
        last = self.state.get("last_shot")
        if not last:
            await self.messenger.send("No shot logged yet.")
            return
        notes = self.state.get("last_notes", {})
        bits = [f"Shot #{last['shot_id']} — {last.get('profile', '?')}"]
        bits.append(f"{last.get('duration_ms', 0) / 1000:.0f}s")
        if last.get("volume_g"):
            bits.append(f"{last['volume_g']:.1f} g")
        if notes.get("beanType"):
            bits.append(notes["beanType"])
        text = " · ".join(bits)
        if self.config.journal_url:
            text += f"\n{self.config.journal_url.rstrip('/')}/#{last['shot_id']:06d}"
        await self.messenger.send(text)

    async def _cmd_fix(self) -> None:
        last = self.state.get("last_shot")
        if not last:
            await self.messenger.send("No shot to fix yet.")
            return
        await self.messenger.send(f"✏️ Let's redo shot #{last['shot_id']}:")
        await self.convo.start_shot(
            last["shot_id"], last.get("profile", ""),
            last.get("duration_ms", 0), last.get("volume_g", 0.0),
        )

    async def _cmd_newbag(self) -> None:
        from . import bags

        usage = "Usage: /newbag <grams> [name] — e.g. /newbag 250 Mondo Classico"
        if not self._args:
            await self.messenger.send(usage)
            return
        try:
            grams = float(self._args[0].replace("g", ""))
        except ValueError:
            await self.messenger.send(usage)
            return
        name = (
            " ".join(self._args[1:])
            or self.state.get("last_notes", {}).get("beanType")
            or "Unnamed beans"
        )
        await self.messenger.send(bags.open_bag(self.state, grams, name))

    async def _cmd_bag(self) -> None:
        from . import bags

        await self.messenger.send(bags.bag_status(self.state))

    async def _cmd_tossbag(self) -> None:
        from . import bags

        await self.messenger.send(bags.toss_bag(self.state, " ".join(self._args) or None))

    async def _cmd_digest(self) -> None:
        text = await build_digest(self.client, self.config)
        await self.messenger.send(text or "No shots in the last 7 days. The machine misses you.")

    # ------------------------------------------------------------- frames

    async def on_frame(self, frame: dict[str, Any]) -> None:
        """Called for every status frame: ready ping after /wake, tank watch."""
        if frame.get("tp") != "evt:status":
            return
        await self._check_water(frame)
        if not self._awaiting_ready:
            return
        ct, tt = frame.get("ct", 0), frame.get("tt", 0)
        if frame.get("m") == 1 and tt >= 60 and ct >= tt - 1.0:
            self._awaiting_ready = False
            text = f"☕ {ct:.1f}°C — the machine is ready when you are."
            wl = frame.get("wl")
            if wl is not None and self.config.water_warn_pct and wl < self.config.water_warn_pct:
                text += f" The tank is at {wl}%, though."
            await self.messenger.send(text)

    async def _check_water(self, frame: dict[str, Any]) -> None:
        """Warn once when the tank runs low; re-arm after a refill."""
        wl = frame.get("wl")
        threshold = self.config.water_warn_pct
        if wl is None or not threshold:
            return
        warned = self.state.get("water_warned", False)
        if not warned and wl < threshold:
            self.state.set("water_warned", True)
            await self.messenger.send(
                f"💧 Water tank at {wl}% — maybe top it up before the next shot."
            )
        elif warned and wl >= threshold + 10:
            self.state.set("water_warned", False)


async def build_digest(client, config) -> str | None:
    """Digest from the machine index, falling back to the local journal data."""
    from datetime import datetime
    from pathlib import Path

    from . import digest

    rows = None
    try:
        rows = digest.rows_from_index(await client.fetch_index())
    except Exception:  # noqa: BLE001 - machine may be off
        site_index = Path(config.data_repo or "") / "docs" / "index.json"
        if config.data_repo and site_index.exists():
            rows = digest.rows_from_site_index(site_index)
    if rows is None:
        return None
    return digest.compute(rows, now=datetime.now(), journal_url=config.journal_url)


def make_frame_cache():
    """Returns (update, get): cache the newest status frame with a timestamp."""
    box: dict[str, Any] = {"frame": None, "ts": 0.0}

    def update(frame: dict) -> None:
        if frame.get("tp") == "evt:status":
            box["frame"] = frame
            box["ts"] = time.monotonic()

    def get():
        age = time.monotonic() - box["ts"] if box["frame"] else 1e9
        return box["frame"], age

    return update, get
