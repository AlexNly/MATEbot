"""Optional camera module: MATEbot serves a phone-camera page and records shots.

Strictly opt-in (``MATEBOT_CAMERA=1``). The phone opens the page (HTTPS via
the user's reverse proxy — getUserMedia requires a secure context), grants
camera access and keeps the page open near the machine. When a shot starts,
the server tells the page to record; MediaRecorder chunks stream back over
the WebSocket. When the shot ends (+ a short tail), the clip is transcoded
and attached to the shot via the video pipeline.

Best-effort by design: no page connected → no recording, no errors.
"""

from __future__ import annotations

import asyncio
import importlib.resources
import json
import logging
import tempfile
from pathlib import Path

from aiohttp import WSMsgType, web

log = logging.getLogger(__name__)

TAIL_SECONDS = 5.0
MAX_RECORD_SECONDS = 240


class CameraServer:
    def __init__(self, port: int, on_clip) -> None:
        """*on_clip* is ``async (webm_path: Path) -> None`` — called per finished clip."""
        self.port = port
        self.on_clip = on_clip
        self._ws: web.WebSocketResponse | None = None
        self._chunks: list[bytes] = []
        self._recording = False
        self._runner: web.AppRunner | None = None
        self._stop_task: asyncio.Task | None = None

    # ------------------------------------------------------------ lifecycle

    async def start(self) -> None:
        app = web.Application()
        app.router.add_get("/", self._page)
        app.router.add_get("/ws", self._websocket)
        self._runner = web.AppRunner(app)
        await self._runner.setup()
        site = web.TCPSite(self._runner, "0.0.0.0", self.port)
        await site.start()
        log.info("camera server listening on :%d", self.port)

    async def stop(self) -> None:
        if self._runner:
            await self._runner.cleanup()

    @property
    def page_connected(self) -> bool:
        return self._ws is not None and not self._ws.closed

    # ------------------------------------------------------------ recording

    async def shot_started(self) -> None:
        if not self.page_connected or self._recording:
            return
        self._chunks = []
        self._recording = True
        await self._send({"cmd": "start"})
        log.info("camera: recording started")
        self._stop_task = asyncio.create_task(self._safety_stop())

    async def shot_ended(self) -> None:
        if not self._recording:
            return
        await asyncio.sleep(TAIL_SECONDS)
        await self._finish()

    async def _safety_stop(self) -> None:
        await asyncio.sleep(MAX_RECORD_SECONDS)
        if self._recording:
            log.warning("camera: safety stop after %ds", MAX_RECORD_SECONDS)
            await self._finish()

    async def _finish(self) -> None:
        if not self._recording:
            return
        self._recording = False
        if self._stop_task and not self._stop_task.done():
            self._stop_task.cancel()
        await self._send({"cmd": "stop"})
        # give the page a moment to flush its final chunks
        for _ in range(20):
            await asyncio.sleep(0.25)
            if not self.page_connected:
                break
        chunks, self._chunks = self._chunks, []
        if not chunks:
            log.info("camera: no video data received")
            return
        with tempfile.NamedTemporaryFile(suffix=".webm", delete=False) as tmp:
            for chunk in chunks:
                tmp.write(chunk)
            path = Path(tmp.name)
        log.info("camera: clip complete (%.1f MB)", path.stat().st_size / 1e6)
        try:
            await self.on_clip(path)
        finally:
            path.unlink(missing_ok=True)

    # ------------------------------------------------------------ http

    async def _page(self, request: web.Request) -> web.Response:
        html = (importlib.resources.files("matebot") / "web_cam" / "index.html").read_text()
        return web.Response(text=html, content_type="text/html")

    async def _websocket(self, request: web.Request) -> web.WebSocketResponse:
        ws = web.WebSocketResponse(max_msg_size=8 * 2**20)
        await ws.prepare(request)
        if self._ws and not self._ws.closed:
            await self._ws.close()  # newest page wins
        self._ws = ws
        log.info("camera page connected from %s", request.remote)
        try:
            async for msg in ws:
                if msg.type == WSMsgType.BINARY:
                    if self._recording or self._chunks is not None:
                        self._chunks.append(msg.data)
                elif msg.type == WSMsgType.TEXT:
                    log.debug("camera page: %s", msg.data[:100])
        finally:
            if self._ws is ws:
                self._ws = None
            log.info("camera page disconnected")
        return ws

    async def _send(self, payload: dict) -> None:
        if self.page_connected:
            try:
                await self._ws.send_str(json.dumps(payload))
            except Exception as exc:  # noqa: BLE001
                log.warning("camera send failed: %s", exc)
