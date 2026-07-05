import pytest

from gaggibot.commands import CommandRouter, make_frame_cache
from gaggibot.config import Config
from gaggibot.machine import MachineError
from gaggibot.state import State


class FakeMessenger:
    def __init__(self):
        self.sent = []

    async def send(self, text, options=None):
        self.sent.append(text)
        return "1"


class FakeClient:
    def __init__(self, connected=True):
        self.connected = connected
        self.requests = []

    async def request(self, tp, **fields):
        if not self.connected:
            raise MachineError("websocket not connected")
        self.requests.append((tp, fields))
        return {"msg": "Ok"}


class FakeConvo:
    def __init__(self):
        self.started = []

    async def start_shot(self, *args):
        self.started.append(args)


@pytest.fixture
def setup(tmp_path):
    client = FakeClient()
    state = State(tmp_path / "state.json")
    convo = FakeConvo()
    fm = FakeMessenger()
    cache, latest = make_frame_cache()
    router = CommandRouter(client, state, convo, fm, Config(), latest)
    return router, client, state, convo, fm, cache


@pytest.mark.asyncio
async def test_wake_and_ready_ping(setup):
    router, client, state, convo, fm, cache = setup
    assert await router.handle("/wake")
    assert client.requests == [("req:change-mode", {"mode": 1})]
    assert "Waking" in fm.sent[-1]

    # heating: no ping yet
    await router.on_frame({"tp": "evt:status", "m": 1, "ct": 70.0, "tt": 93.0})
    assert len(fm.sent) == 1
    # at temperature: ping exactly once
    await router.on_frame({"tp": "evt:status", "m": 1, "ct": 92.4, "tt": 93.0})
    await router.on_frame({"tp": "evt:status", "m": 1, "ct": 92.8, "tt": 93.0})
    assert sum("ready" in t for t in fm.sent) == 1


@pytest.mark.asyncio
async def test_sleep(setup):
    router, client, *_ , fm, _ = setup
    assert await router.handle("/sleep")
    assert client.requests == [("req:change-mode", {"mode": 0})]


@pytest.mark.asyncio
async def test_wake_with_machine_off(setup):
    router, client, state, convo, fm, cache = setup
    client.connected = False
    assert await router.handle("/wake")
    assert "Can't reach the machine" in fm.sent[-1]


@pytest.mark.asyncio
async def test_status_online_and_offline(setup):
    router, client, state, convo, fm, cache = setup
    await router.handle("/status")
    assert "offline" in fm.sent[-1]
    cache({"tp": "evt:status", "m": 1, "ct": 92.1, "tt": 93.0, "wl": 60})
    await router.handle("/status")
    assert "brew mode" in fm.sent[-1] and "92.1" in fm.sent[-1] and "60%" in fm.sent[-1]


@pytest.mark.asyncio
async def test_last_and_fix(setup):
    router, client, state, convo, fm, cache = setup
    await router.handle("/last")
    assert "No shot" in fm.sent[-1]

    state.set("last_shot", {"shot_id": 60, "profile": "Direct Lever v3",
                            "duration_ms": 16000, "volume_g": 35.8})
    router.config.journal_url = "https://example.github.io/journal/"
    await router.handle("/last")
    assert "Shot #60" in fm.sent[-1] and "#000060" in fm.sent[-1]

    await router.handle("/fix")
    assert convo.started == [(60, "Direct Lever v3", 16000, 35.8)]


@pytest.mark.asyncio
async def test_unknown_command_not_consumed(setup):
    router, *_ = setup
    assert not await router.handle("/definitelynotacommand")


@pytest.mark.asyncio
async def test_telegram_suffix_stripped(setup):
    router, client, *_ = setup
    assert await router.handle("/sleep@Gaggi0614bot")
    assert client.requests[-1][0] == "req:change-mode"
