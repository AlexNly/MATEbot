import pathlib

import pytest

from gaggibot.watcher import ShotWatcher, replay_frames
from tests.test_watcher import FakeClient

FIXTURE = pathlib.Path(__file__).parent / "fixtures" / "status_frames.jsonl"


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
