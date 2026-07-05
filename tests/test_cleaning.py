import pathlib
import struct

import pytest

from gaggibot.slog import parse_index
from gaggibot.watcher import ShotWatcher, replay_frames

FIXTURE = pathlib.Path(__file__).parent / "fixtures" / "status_frames.jsonl"


class FakeClient:
    """Index with one existing shot; a new one appears on the second poll."""

    def __init__(self):
        self.polls = 0

    async def fetch_index(self):
        self.polls += 1
        header = struct.pack("<IHHII16x", 0x58444953, 1, 128, 2 if self.polls > 1 else 1, 60)
        entry = lambda sid: struct.pack(  # noqa: E731
            "<IIIHBB32s48s32x", sid, 1700000000, 30000, 361, 0, 0x01, b"p", b"Direct Lever v3"
        )
        blob = header + entry(58) + (entry(59) if self.polls > 1 else b"")
        return parse_index(blob)


@pytest.mark.asyncio
async def test_on_utility_fires_for_backflush_only():
    seen = []

    async def on_utility(profile):
        seen.append(profile)

    watcher = ShotWatcher(FakeClient(), min_duration_s=10, on_utility=on_utility)
    _ = [s async for s in watcher.shots(replay_frames(FIXTURE))]
    # fixture contains one 40s backflush (fires) and one 5s short brew (too
    # short, never counts as a utility run)
    assert seen == ["[Utility] Backflush"]
