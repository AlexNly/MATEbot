import asyncio
import socket

import aiohttp
import pytest

from matebot import camera as camera_mod
from matebot.camera import CameraServer


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.mark.asyncio
async def test_full_recording_cycle(monkeypatch):
    monkeypatch.setattr(camera_mod, "TAIL_SECONDS", 0.05)
    clips = []

    async def on_clip(path):
        clips.append(path.read_bytes())

    port = free_port()
    server = CameraServer(port, on_clip)
    await server.start()
    try:
        async with aiohttp.ClientSession() as session:
            # the page itself is served
            async with session.get(f"http://127.0.0.1:{port}/") as resp:
                assert resp.status == 200
                assert "MATEbot Cam" in await resp.text()

            async with session.ws_connect(f"http://127.0.0.1:{port}/ws") as ws:
                assert server.page_connected

                await server.shot_started()
                msg = await asyncio.wait_for(ws.receive_json(), 5)
                assert msg == {"cmd": "start"}

                await ws.send_bytes(b"CHUNK1")
                await ws.send_bytes(b"CHUNK2")
                await asyncio.sleep(0.1)  # let the server ingest

                ended = asyncio.create_task(server.shot_ended())
                msg = await asyncio.wait_for(ws.receive_json(), 5)
                assert msg == {"cmd": "stop"}
                await ws.close()  # page done flushing
                await asyncio.wait_for(ended, 10)

        assert clips == [b"CHUNK1CHUNK2"]
    finally:
        await server.stop()


@pytest.mark.asyncio
async def test_no_page_no_recording(monkeypatch):
    monkeypatch.setattr(camera_mod, "TAIL_SECONDS", 0.05)
    clips = []

    async def on_clip(path):
        clips.append(path)

    server = CameraServer(free_port(), on_clip)
    await server.start()
    try:
        await server.shot_started()   # no page connected: silently ignored
        await server.shot_ended()
        assert clips == []
        assert not server.page_connected
    finally:
        await server.stop()
