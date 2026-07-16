import aiohttp
import pytest

from matebot import sync as sync_mod
from matebot.state import State
from matebot.sync import sync_soon


class Notify:
    def __init__(self):
        self.sent = []

    async def __call__(self, text):
        self.sent.append(text)


@pytest.mark.asyncio
async def test_machine_offline_sets_pending_flag(tmp_path, monkeypatch):
    async def boom(client, repo, site_title="x", video_keep=15):
        raise aiohttp.ClientConnectionError("machine off")

    monkeypatch.setattr(sync_mod, "sync", boom)
    state = State(tmp_path / "state.json")
    notify = Notify()
    await sync_soon(None, tmp_path, notify, state=state)
    assert state.get("sync_pending") is True
    assert "offline" in notify.sent[0]

    # quiet retry path: no message even on repeated failure
    await sync_soon(None, tmp_path, notify, state=state, quiet=True)
    assert len(notify.sent) == 1


@pytest.mark.asyncio
async def test_success_clears_pending_flag(tmp_path, monkeypatch):
    async def ok(client, repo, site_title="x", video_keep=15):
        return True

    monkeypatch.setattr(sync_mod, "sync", ok)
    state = State(tmp_path / "state.json")
    state.set("sync_pending", True)
    await sync_soon(None, tmp_path, Notify(), state=state)
    assert state.get("sync_pending") is False
