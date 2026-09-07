"""Replay split status through the actual CLI, watcher and questionnaire."""

import asyncio
import json

import pytest

from matebot.cli import _run
from matebot.config import Config
from matebot.slog import IndexEntry, ShotIndex
from matebot.status import DISCONNECTED


@pytest.mark.parametrize('utility,disconnect', [(False, False), (True, False), (False, True)])
async def test_replay_camera_and_questionnaire(tmp_path, monkeypatch, utility, disconnect):
    from matebot import camera, machine, messengers

    calls = []
    sent = []

    class Client:
        def __init__(self, host):
            self.polls = 0

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            pass

        async def fetch_index(self):
            self.polls += 1
            entries = [] if self.polls == 1 else [
                IndexEntry(1, 1700000000, 30000, 36, 0, 1, 'classic', 'Classic')]
            return ShotIndex(1, 2, entries)

    class Camera:
        def __init__(self, *args):
            pass

        async def start(self):
            pass

        async def stop(self):
            pass

        async def shot_started(self):
            calls.append('start')

        async def shot_ended(self):
            calls.append('end')

    class Messenger:
        async def start(self):
            pass

        async def stop(self):
            pass

        async def send(self, text, options=None):
            sent.append(text)
            return '1'

        async def events(self):
            await asyncio.Event().wait()
            if False:
                yield

    monkeypatch.setattr(machine, 'GaggiMateClient', Client)
    monkeypatch.setattr(camera, 'CameraServer', Camera)
    monkeypatch.setattr(messengers, 'create_messenger', lambda config: Messenger())
    frames = [
        {'tp': 'evt:status', 'm': 1, 'p': 'Classic'},
        {'tp': 'evt:status', 'ct': 92, 'process': {'a': 1, 's': 'brew', 'e': 30000,
                                                 'u': int(utility)}},
        {'tp': 'evt:status', 'm': 1, 'p': 'Classic'},
        {'tp': 'evt:history-shot-saved', 'id': 1},
    ]
    if disconnect:
        frames.extend([{'tp': DISCONNECTED}, {'tp': 'evt:status', 'm': 1}])
    frames.extend([{'tp': 'evt:status', 'process': None},
                   {'tp': 'evt:status', 'process': None}])
    path = tmp_path / 'frames.jsonl'
    path.write_text('\n'.join(json.dumps(f) for f in frames))
    config = Config(state_dir=str(tmp_path / 'state'), data_repo=str(tmp_path / 'repo'),
                    camera_enabled=True, digest_enabled=False, plots_enabled=False,
                    sync_enabled=False, water_warn_pct=0)
    assert await _run(config, replay=str(path), dry_run=False) == 0
    await asyncio.sleep(0)  # scheduled camera tail callback
    assert calls == ([] if utility else ['start', 'end'])
    assert sum('How was it?' in text for text in sent) == (0 if utility or disconnect else 1)
