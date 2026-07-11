import pytest

from matebot.commands import CommandRouter, make_frame_cache
from matebot.config import Config
from matebot.machine import MachineError
from matebot.state import State


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

    async def send_event(self, tp, **fields):
        if not self.connected:
            raise MachineError("the machine looks powered off")
        self.requests.append((tp, fields))


class FakeConvo:
    def __init__(self):
        self.started = []

    async def start_shot(self, *args, photo=None):
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
    cache({"tp": "evt:status", "m": 0, "ct": 60.0, "tt": 0})  # machine online
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
async def test_wake_with_machine_off_arms_pending(setup):
    router, client, state, convo, fm, cache = setup
    client.connected = False
    assert await router.handle("/wake")  # no hook configured, machine off
    assert "looks powered off" in fm.sent[-1]
    assert state.get("pending_wake_until", 0) > 0


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


@pytest.mark.asyncio
async def test_wake_hook_runs_and_cold_start_waits(tmp_path, setup):
    router, client, state, convo, fm, cache = setup
    marker = tmp_path / "on"
    router.config.wake_hook = f"touch {marker}"
    # machine already online: hook runs, no boot wait
    cache({"tp": "evt:status", "m": 0, "ct": 60.0, "tt": 0})
    await router.handle("/wake")
    assert marker.exists()
    assert client.requests[-1] == ("req:change-mode", {"mode": 1})
    assert not any("waiting for the machine to boot" in t for t in fm.sent)


@pytest.mark.asyncio
async def test_wake_hook_failure_still_arms_pending(setup, monkeypatch):
    router, client, state, convo, fm, cache = setup
    router.config.wake_hook = "exit 1"

    async def no_sleep(_):
        return None

    monkeypatch.setattr("matebot.commands.asyncio.sleep", no_sleep)
    await router.handle("/wake")  # machine offline, hook broken
    assert client.requests == []
    assert any("Couldn't reach the plug" in t for t in fm.sent)
    assert state.get("pending_wake_until", 0) > 0

    # machine gets powered manually -> the armed wake still heats it
    await router.on_machine_online({"tp": "evt:status", "m": 0, "ct": 25.0, "tt": 0})
    assert client.requests == [("req:change-mode", {"mode": 1})]
    assert any("Machine is up" in t for t in fm.sent)
    # consumed: a second appearance does nothing
    await router.on_machine_online({"tp": "evt:status", "m": 0, "ct": 25.0, "tt": 0})
    assert len(client.requests) == 1


@pytest.mark.asyncio
async def test_wake_cold_start_event_driven(setup):
    router, client, state, convo, fm, cache = setup
    router.config.wake_hook = "true"
    await router.handle("/wake")  # machine offline, hook fine
    assert client.requests == []  # nothing sent inline
    assert any("moment the machine is up" in t for t in fm.sent)
    await router.on_machine_online({"tp": "evt:status", "m": 0, "ct": 25.0, "tt": 0})
    assert client.requests == [("req:change-mode", {"mode": 1})]


@pytest.mark.asyncio
async def test_pending_wake_expires(setup):
    import time as _time

    router, client, state, convo, fm, cache = setup
    state.set("pending_wake_until", _time.time() - 1)  # already expired
    await router.on_machine_online({"tp": "evt:status", "m": 0, "ct": 25.0, "tt": 0})
    assert client.requests == []


@pytest.mark.asyncio
async def test_sleep_hook_cuts_power(monkeypatch, tmp_path, setup):
    router, client, state, convo, fm, cache = setup
    marker = tmp_path / "off"
    router.config.sleep_hook = f"touch {marker}"

    async def no_sleep(_):
        return None

    monkeypatch.setattr("matebot.commands.asyncio.sleep", no_sleep)
    await router.handle("/sleep")
    assert client.requests[-1] == ("req:change-mode", {"mode": 0})
    assert marker.exists()
    assert any("Fully dark" in t for t in fm.sent)


@pytest.mark.asyncio
async def test_sleep_hook_works_when_machine_already_off(monkeypatch, tmp_path, setup):
    router, client, state, convo, fm, cache = setup
    client.connected = False
    marker = tmp_path / "off"
    router.config.sleep_hook = f"touch {marker}"

    async def no_sleep(_):
        return None

    monkeypatch.setattr("matebot.commands.asyncio.sleep", no_sleep)
    await router.handle("/sleep")
    assert marker.exists()  # plug still gets cut even though the WS is down


@pytest.mark.asyncio
async def test_low_water_warns_once_and_rearms(setup):
    router, client, state, convo, fm, cache = setup
    frame = lambda wl: {"tp": "evt:status", "m": 0, "ct": 60.0, "tt": 0, "wl": wl}  # noqa: E731
    await router.on_frame(frame(40))
    assert fm.sent == []
    await router.on_frame(frame(12))
    await router.on_frame(frame(9))   # still low: no second warning
    assert sum("Water tank" in t for t in fm.sent) == 1
    await router.on_frame(frame(80))  # refilled: re-armed
    await router.on_frame(frame(10))
    assert sum("Water tank" in t for t in fm.sent) == 2


@pytest.mark.asyncio
async def test_low_water_disabled(setup):
    router, client, state, convo, fm, cache = setup
    router.config.water_warn_pct = 0
    await router.on_frame({"tp": "evt:status", "m": 0, "ct": 60.0, "tt": 0, "wl": 3})
    assert fm.sent == []


@pytest.mark.asyncio
async def test_ready_ping_mentions_low_tank(setup):
    router, client, state, convo, fm, cache = setup
    cache({"tp": "evt:status", "m": 0, "ct": 60.0, "tt": 0})  # machine online
    await router.handle("/wake")
    await router.on_frame({"tp": "evt:status", "m": 1, "ct": 92.5, "tt": 93.0, "wl": 40})
    ready = [t for t in fm.sent if "ready when you are" in t]
    assert ready and "tank" not in ready[0]

    router._awaiting_ready = True
    state.set("water_warned", True)  # already warned; ping still mentions it
    await router.on_frame({"tp": "evt:status", "m": 1, "ct": 92.6, "tt": 93.0, "wl": 8})
    assert any("The tank is at 8%" in t for t in fm.sent)


def test_in_window():
    from matebot.commands import in_window

    assert in_window("06:30-07:00", "06:30")
    assert in_window("06:30-07:00", "06:59")
    assert not in_window("06:30-07:00", "07:00")  # end exclusive
    assert not in_window("06:30-07:00", "06:29")
    assert not in_window("", "06:45")
    assert not in_window("garbage", "06:45")


@pytest.mark.asyncio
async def test_autoheat_once_per_day(setup, monkeypatch):
    router, client, state, convo, fm, cache = setup
    router.config.autoheat_window = "06:30-07:00"

    class FakeDT:
        @staticmethod
        def now():
            import datetime as _dt

            return _dt.datetime(2026, 7, 9, 6, 50)

    monkeypatch.setattr("matebot.commands.datetime", FakeDT)
    standby = {"tp": "evt:status", "m": 0, "ct": 22.0, "tt": 0}

    await router.on_machine_online(standby)
    assert client.requests == [("req:change-mode", {"mode": 1})]
    assert any("Good morning" in t for t in fm.sent)

    await router.on_machine_online(standby)  # second power-on same day: no-op
    assert len(client.requests) == 1

    # already brewing: never override
    state.set("autoheat_date", None)
    await router.on_machine_online({"tp": "evt:status", "m": 1, "ct": 90.0, "tt": 93.0})
    assert len(client.requests) == 1


@pytest.mark.asyncio
async def test_autoheat_outside_window_or_disabled(setup, monkeypatch):
    router, client, state, convo, fm, cache = setup

    class FakeDT:
        @staticmethod
        def now():
            import datetime as _dt

            return _dt.datetime(2026, 7, 9, 9, 15)

    monkeypatch.setattr("matebot.commands.datetime", FakeDT)
    standby = {"tp": "evt:status", "m": 0, "ct": 22.0, "tt": 0}
    router.config.autoheat_window = "06:30-07:00"
    await router.on_machine_online(standby)   # 09:15: outside window
    router.config.autoheat_window = ""
    await router.on_machine_online(standby)   # disabled
    assert client.requests == []


@pytest.mark.asyncio
async def test_brew_enforcer_resends_until_confirmed(setup, monkeypatch):
    router, client, state, convo, fm, cache = setup
    clock = {"t": 1000.0}
    monkeypatch.setattr("matebot.commands.time.monotonic", lambda: clock["t"])

    cache({"tp": "evt:status", "m": 0, "ct": 60.0, "tt": 0})
    await router.handle("/wake")  # machine online -> first send + enforcer armed
    assert client.requests == [("req:change-mode", {"mode": 1})]

    standby = {"tp": "evt:status", "m": 0, "ct": 25.0, "tt": 0}
    await router.on_frame(standby)  # 0s later: too soon to resend
    assert len(client.requests) == 1

    clock["t"] += 9  # command was swallowed during boot; resend kicks in
    await router.on_frame(standby)
    assert len(client.requests) == 2

    await router.on_frame({"tp": "evt:status", "m": 1, "ct": 30.0, "tt": 93.0})  # confirmed
    clock["t"] += 9
    await router.on_frame({"tp": "evt:status", "m": 1, "ct": 40.0, "tt": 93.0})
    assert len(client.requests) == 2  # no more resends after confirmation

    # ready ping still works at temperature
    await router.on_frame({"tp": "evt:status", "m": 1, "ct": 92.4, "tt": 93.0})
    assert any("ready when you are" in t for t in fm.sent)


@pytest.mark.asyncio
async def test_brew_enforcer_gives_up_loudly(setup, monkeypatch):
    router, client, state, convo, fm, cache = setup
    clock = {"t": 1000.0}
    monkeypatch.setattr("matebot.commands.time.monotonic", lambda: clock["t"])

    cache({"tp": "evt:status", "m": 0, "ct": 60.0, "tt": 0})
    await router.handle("/wake")
    clock["t"] += 91  # machine never leaves standby
    await router.on_frame({"tp": "evt:status", "m": 0, "ct": 25.0, "tt": 0})
    assert any("won't switch to brew" in t for t in fm.sent)
    # enforcer disarmed: nothing further happens
    clock["t"] += 9
    await router.on_frame({"tp": "evt:status", "m": 0, "ct": 25.0, "tt": 0})
    assert len(client.requests) == 1
