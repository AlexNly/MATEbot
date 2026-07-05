import pathlib
import struct

import pytest

from gaggibot.slog import parse_index
from gaggibot.watcher import ShotWatcher, replay_frames

FIXTURE = pathlib.Path(__file__).parent / "fixtures" / "status_frames.jsonl"


def make_index(next_id: int, ids_flags: list[tuple[int, int]]) -> bytes:
    header = struct.pack("<IHHII16x", 0x58444953, 1, 128, len(ids_flags), next_id)
    entries = b"".join(
        struct.pack("<IIIHBB32s48s32x", sid, 1700000000 + sid, 30000, 361, 0, flags,
                    b"prof", b"Direct Lever [Automatic Pro] v3")
        for sid, flags in ids_flags
    )
    return header + entries


class FakeClient:
    """Stands in for GaggiMateClient: index grows by one shot after the first poll."""

    def __init__(self, start_id: int = 58):
        self.start_id = start_id
        self.polls = 0

    async def fetch_index(self):
        self.polls += 1
        ids = [(self.start_id, 0x01)]
        if self.polls > 1:  # new shot appears on second poll
            ids.append((self.start_id + 1, 0x01))
        return parse_index(make_index(self.start_id + 2, ids))


@pytest.mark.asyncio
async def test_detects_exactly_one_valid_shot():
    client = FakeClient()
    watcher = ShotWatcher(client, min_duration_s=10)
    found = [s async for s in watcher.shots(replay_frames(FIXTURE))]
    # fixture contains: one 30s brew (valid), one 5s brew (short),
    # one 40s backflush (ignored profile), one steam process (wrong mode)
    assert len(found) == 1
    assert found[0].entry.id == 59
    assert "Direct Lever" in found[0].profile_label


@pytest.mark.asyncio
async def test_incomplete_entries_are_not_resolved():
    class NeverCompletes(FakeClient):
        async def fetch_index(self):
            return parse_index(make_index(60, [(58, 0x01), (59, 0x00)]))  # 59 not completed

    watcher = ShotWatcher(NeverCompletes(), min_duration_s=10)
    watcher.last_known_id = 58
    entry = await watcher._resolve_new_entry(budget_s=0.2, poll_s=0.1)
    assert entry is None


@pytest.mark.asyncio
async def test_deleted_entries_are_skipped():
    class DeletedOnly(FakeClient):
        async def fetch_index(self):
            return parse_index(make_index(60, [(58, 0x01), (59, 0x01 | 0x02)]))

    watcher = ShotWatcher(DeletedOnly(), min_duration_s=10)
    watcher.last_known_id = 58
    entry = await watcher._resolve_new_entry(budget_s=0.2, poll_s=0.1)
    assert entry is None
