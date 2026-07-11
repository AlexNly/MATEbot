"""Command-routing smoke test for Discord: /help, /status, /bag as plain text."""
import asyncio
import logging
import os
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).parent.parent / "src"))

from matebot.commands import CommandRouter, make_frame_cache  # noqa: E402
from matebot.config import Config  # noqa: E402
from matebot.messengers.base import TextReply  # noqa: E402
from matebot.messengers.discord import DiscordMessenger  # noqa: E402
from matebot.state import State  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s: %(message)s")
log = logging.getLogger("cmdsmoke")


class FakeMachine:
    async def request(self, tp, **f):
        return {"msg": "Ok"}

    async def send_event(self, tp, **f):
        log.info("MACHINE would receive: %s %s", tp, f)


async def main():
    messenger = DiscordMessenger(
        os.environ["DISCORD_BOT_TOKEN"], os.environ["DISCORD_CHANNEL_ID"]
    )
    state = State("/tmp/discord-cmd-state.json")
    state.set("last_shot", {"shot_id": 73, "profile": "Direct Lever v3",
                            "duration_ms": 42600, "volume_g": 36.4})
    cache, latest = make_frame_cache()
    cache({"tp": "evt:status", "m": 0, "ct": 91.2, "tt": 0, "wl": 55})
    router = CommandRouter(FakeMachine(), state, None, messenger,
                           Config(journal_url="https://alexnly.github.io/GAGGIMATE-0614/"), latest)
    await messenger.start()
    await messenger.send("Command smoke test ready — send /help, /status, /last, /wake as messages.")

    async def pump():
        handled = 0
        async for event in messenger.events():
            log.info("EVENT: %r", event)
            if isinstance(event, TextReply) and event.text.strip().startswith("/"):
                consumed = await router.handle(event.text.strip())
                log.info("router consumed=%s", consumed)
                handled += 1
                if handled >= 4:
                    log.info("✅ command routing works on Discord")
                    return

    try:
        await asyncio.wait_for(pump(), timeout=600)
    except TimeoutError:
        log.error("timed out")
    finally:
        await messenger.stop()


asyncio.run(main())
