"""Exercise the production WebSocket stream without a machine or messenger."""

import asyncio
import json
from types import SimpleNamespace

import aiohttp

from matebot.machine import GaggiMateClient
from matebot.status import DISCONNECTED


class Socket:
    def __init__(self, frames):
        self.frames = iter(frames)
        self.closed = False

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        self.closed = True

    async def receive(self, **kwargs):
        frame = next(self.frames)
        if frame is None:
            return SimpleNamespace(type=aiohttp.WSMsgType.CLOSED)
        return SimpleNamespace(type=aiohttp.WSMsgType.TEXT, data=json.dumps(frame))


async def test_live_stream_merge_request_correlation_and_reconnect():
    sockets = iter([
        Socket([
            {'tp': 'evt:status', 'm': 1, 'p': 'Classic'},
            {'tp': 'evt:status', 'ct': 92, 'process': {'a': 1}},
            {'tp': 'res:profiles:list', 'rid': 'test', 'profiles': []},
            None,
        ]),
        Socket([{'tp': 'evt:status', 'ct': 20},
                {'tp': 'evt:status', 'm': 0, 'process': None}]),
    ])
    client = GaggiMateClient('unused.invalid')
    client._session = SimpleNamespace(ws_connect=lambda *a, **kw: next(sockets))
    pending = asyncio.get_running_loop().create_future()
    client._pending['test'] = pending
    stream = client.status_stream()
    try:
        first = await anext(stream)
        second = await anext(stream)
        response = await anext(stream)
        assert await pending == response
        assert first == {'tp': 'evt:status', 'm': 1, 'p': 'Classic'}
        assert second['m'] == 1 and second['ct'] == 92
        assert 'process' not in first
        assert await anext(stream) == {'tp': DISCONNECTED}
        client.nudge()  # reconnect without waiting for backoff
        assert await anext(stream) == {'tp': 'evt:status', 'ct': 20}
        last = await anext(stream)
        assert last['m'] == 0 and last['process'] is None and 'p' not in last
    finally:
        await stream.aclose()
