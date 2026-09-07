import pathlib
import struct

import pytest

from matebot.slog import parse_index
from matebot.watcher import ShotWatcher, replay_frames

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


class StaleThenFreshClient:
    """Reproduces the morning-of-July-9 bug: a stale completed entry (an
    aborted 8s shot) sits in the index while the real shot's entry only
    completes on a later poll."""

    def __init__(self):
        self.polls = 0

    async def fetch_index(self):
        self.polls += 1
        header = struct.pack("<IHHII16x", 0x58444953, 1, 128, 3, 74)
        def entry(sid, dur_ms, flags):
            return struct.pack("<IIIHBB32s48s32x", sid, 1700000000, dur_ms, 360, 0, flags,
                               b"p", b"Direct Lever v3")
        e71 = entry(71, 8300, 0x01)                        # stale failed shot, completed
        e72 = entry(72, 29000, 0x01 if self.polls > 2 else 0x00)  # real shot completes late
        return parse_index(header + entry(70, 30000, 0x01) + e71 + e72)


@pytest.mark.asyncio
async def test_resolution_skips_stale_entry_and_waits_for_matching_duration():
    watcher = ShotWatcher(StaleThenFreshClient(), min_duration_s=10)
    watcher.last_known_id = 70
    entry = await watcher._resolve_new_entry(duration_hint_ms=25400, budget_s=2.0, poll_s=0.1)
    assert entry is not None and entry.id == 72  # NOT the stale 8.3s shot 71


@pytest.mark.asyncio
async def test_resolution_falls_back_to_newest_when_nothing_matches():
    class OnlyStale:
        async def fetch_index(self):
            header = struct.pack("<IHHII16x", 0x58444953, 1, 128, 1, 72)
            e = struct.pack("<IIIHBB32s48s32x", 71, 1700000000, 8300, 360, 0, 0x01,
                            b"p", b"Direct Lever v3")
            return parse_index(header + e)

    watcher = ShotWatcher(OnlyStale(), min_duration_s=10)
    watcher.last_known_id = 70
    entry = await watcher._resolve_new_entry(duration_hint_ms=42600, budget_s=0.3, poll_s=0.1)
    assert entry is not None and entry.id == 71  # better late than never, logged loudly


@pytest.mark.parametrize('ending', [{'a': 0, 's': 'brew', 'e': 30000}, None])
async def test_split_status_detects_once(tmp_path, ending):
    import json

    frames = [
        {'tp': 'evt:status', 'm': 1, 'p': 'Classic'},
        {'tp': 'evt:status', 'process': {'a': 1, 's': 'brew', 'e': 30000}},
        {'tp': 'evt:status', 'm': 0, 'p': 'Next selection'},
        {'tp': 'res:profiles:list', 'rid': 'unrelated'},
        {'tp': 'evt:status', 'process': ending},
        {'tp': 'evt:status', 'process': ending},
    ]
    path = tmp_path / 'split.jsonl'
    path.write_text('\n'.join(json.dumps(f) for f in frames))
    watcher = ShotWatcher(FakeClient())
    shots = [s async for s in watcher.shots(replay_frames(path))]
    assert len(shots) == 1
    assert shots[0].entry.id == 59
    assert shots[0].profile_label == 'Classic'
    assert shots[0].duration_ms == 30000


@pytest.mark.parametrize('utility,duration,disconnect', [(True, 30000, False),
                                                       (False, 5000, False),
                                                       (False, 30000, True)])
async def test_split_status_ignores_utility_short_and_disconnected_shots(
    tmp_path, utility, duration, disconnect,
):
    import json

    from matebot.status import DISCONNECTED

    frames = [
        {'tp': 'evt:status', 'm': 1, 'p': 'Ordinary name'},
        {'tp': 'evt:status', 'process': {'a': 1, 's': 'brew', 'e': duration, 'u': utility}},
    ]
    if disconnect:
        frames.extend([{'tp': DISCONNECTED}, {'tp': 'evt:status', 'm': 1}])
    frames.append({'tp': 'evt:status', 'process': None})
    path = tmp_path / 'split.jsonl'
    path.write_text('\n'.join(json.dumps(f) for f in frames))
    client = FakeClient()
    assert [s async for s in ShotWatcher(client).shots(replay_frames(path))] == []
    assert client.polls == 1  # startup only; never resolve a false shot
