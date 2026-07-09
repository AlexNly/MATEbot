"""Live smoke test for the Discord messenger backend.

Drives a complete fake-shot questionnaire through real Discord — buttons,
free-text answers, photo, commands — without needing the machine. Saved
notes land in a recorder and are printed instead of hitting hardware.

Usage:
    DISCORD_BOT_TOKEN=... DISCORD_CHANNEL_ID=... python scripts/discord_smoke.py
"""

import asyncio
import logging
import os
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).parent.parent / "src"))

from matebot.conversation import Conversation  # noqa: E402
from matebot.messengers.discord import DiscordMessenger  # noqa: E402
from matebot.state import State  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("smoke")

GOLDEN = pathlib.Path(__file__).parent.parent / "tests" / "fixtures" / "000004.slog"


async def main() -> None:
    messenger = DiscordMessenger(
        os.environ["DISCORD_BOT_TOKEN"], os.environ["DISCORD_CHANNEL_ID"]
    )
    state = State("/tmp/discord-smoke-state.json")
    saved = {}

    async def save_notes(shot_id, notes):
        saved[shot_id] = notes
        log.info("NOTES SAVED for #%s: %s", shot_id, notes)
        return True

    convo = Conversation(messenger, state, save_notes)
    await messenger.start()
    log.info("connected — starting fake questionnaire")

    photo = None
    try:
        from matebot.plot import render_shot_png
        from matebot.slog import parse_slog

        photo = render_shot_png(parse_slog(GOLDEN.read_bytes()), title="Shot #999 — Smoke Test")
        log.info("photo rendered (%d bytes)", len(photo))
    except Exception as exc:  # noqa: BLE001
        log.warning("photo skipped: %s", exc)

    await convo.start_shot(999, "Discord Smoke Test", 31000, 36.2, photo=photo)
    log.info("questionnaire sent — play it through in Discord (buttons AND typed answers)")

    async def pump():
        async for event in messenger.events():
            log.info("EVENT: %r", event)
            await convo.handle_event(event)
            if saved:
                log.info("✅ questionnaire complete — smoke test PASSED")
                return

    try:
        await asyncio.wait_for(pump(), timeout=600)
    except TimeoutError:
        log.error("timed out waiting for interactions")
    finally:
        await messenger.stop()


if __name__ == "__main__":
    asyncio.run(main())
