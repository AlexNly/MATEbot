"""Async client for the GaggiMate controller (WebSocket + HTTP).

Connection notes (learned from firmware source, do not "simplify" away):

* The ESP32's AsyncWebSocket closes clients whenever its send queue fills
  (e.g. someone opens the web UI stats page), and status frames only flow
  while a client is connected. We therefore treat 15 s of silence as a dead
  socket and reconnect with capped exponential backoff.
* Missing files under ``/api/history/`` are served as HTTP 200 with the
  gzipped SPA ``index.html`` (catch-all route) — every download must be
  content-validated, never trusted by status code.
* ``req:history:notes:save`` id semantics: the request-level ``id`` is used
  verbatim as the filename (must be the 6-digit padded id), while the ``id``
  inside the notes payload is unpadded. ``rating`` is a number; all other
  note values must be strings or the firmware silently ignores them.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import random
import uuid
from collections.abc import AsyncIterator
from typing import Any

import aiohttp

from .slog import ShotIndex, is_slog, parse_index

log = logging.getLogger(__name__)

REDACTED_SETTINGS = ("wifiSsid", "wifiPassword", "apPassword", "haPassword")


class MachineError(RuntimeError):
    pass


class GaggiMateClient:
    def __init__(self, host: str, *, request_timeout: float = 15.0) -> None:
        self.host = host
        self.base = f"http://{host}"
        self.ws_url = f"ws://{host}/ws"
        self._timeout = aiohttp.ClientTimeout(total=request_timeout)
        self._session: aiohttp.ClientSession | None = None
        self._ws: aiohttp.ClientWebSocketResponse | None = None
        self._pending: dict[str, asyncio.Future] = {}

    async def __aenter__(self) -> GaggiMateClient:
        self._session = aiohttp.ClientSession(timeout=self._timeout)
        return self

    async def __aexit__(self, *exc) -> None:
        await self.close()

    async def close(self) -> None:
        if self._ws is not None and not self._ws.closed:
            await self._ws.close()
        if self._session is not None:
            await self._session.close()
        self._ws = None

    @property
    def session(self) -> aiohttp.ClientSession:
        if self._session is None:
            raise MachineError("client not started (use 'async with')")
        return self._session

    # ------------------------------------------------------------------ WS

    async def status_stream(
        self, *, liveness_timeout: float = 15.0, max_backoff: float = 60.0
    ) -> AsyncIterator[dict]:
        """Yield every WS message as a dict, reconnecting forever.

        Also resolves rid-correlated ``request()`` futures as responses come in.
        """
        backoff = 1.0
        while True:
            try:
                async with self.session.ws_connect(self.ws_url, heartbeat=None) as ws:
                    self._ws = ws
                    log.info("connected to %s", self.ws_url)
                    backoff = 1.0
                    while True:
                        msg = await ws.receive(timeout=liveness_timeout)
                        if msg.type != aiohttp.WSMsgType.TEXT:
                            raise MachineError(f"ws closed ({msg.type.name})")
                        try:
                            data = json.loads(msg.data)
                        except json.JSONDecodeError:
                            continue
                        rid = data.get("rid")
                        if rid and rid in self._pending:
                            self._pending.pop(rid).set_result(data)
                        yield data
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001 - deliberate: never crash-loop
                log.warning("ws connection lost (%s); retry in %.0fs", exc, backoff)
            finally:
                self._ws = None
                for fut in self._pending.values():
                    if not fut.done():
                        fut.set_exception(MachineError("websocket dropped"))
                self._pending.clear()
            await asyncio.sleep(backoff + random.uniform(0, backoff / 4))
            backoff = min(backoff * 2, max_backoff)

    async def request(self, tp: str, *, timeout: float = 20.0, **fields: Any) -> dict:
        """Send a rid-correlated request over the live WS connection."""
        if self._ws is None or self._ws.closed:
            raise MachineError("websocket not connected")
        rid = f"gb-{uuid.uuid4().hex[:10]}"
        fut: asyncio.Future = asyncio.get_running_loop().create_future()
        self._pending[rid] = fut
        try:
            await self._ws.send_str(json.dumps({"tp": tp, "rid": rid, **fields}))
            return await asyncio.wait_for(fut, timeout)
        finally:
            self._pending.pop(rid, None)

    async def notes_save(self, shot_id: int, notes: dict[str, Any]) -> dict:
        """Write shot notes; lands in the web UI's "Shot Notes" panel."""
        payload = {"id": str(shot_id)}  # unpadded inside the payload
        for key, value in notes.items():
            if value is None:
                continue
            payload[key] = value if key == "rating" else str(value)
        return await self.request(
            "req:history:notes:save", id=f"{shot_id:06d}", notes=payload
        )

    async def profiles_list(self) -> list[dict]:
        resp = await self.request("req:profiles:list", timeout=30.0)
        return resp.get("profiles", [])

    # ---------------------------------------------------------------- HTTP

    async def _get(self, path: str) -> bytes:
        async with self.session.get(f"{self.base}{path}") as resp:
            resp.raise_for_status()
            return await resp.read()

    async def fetch_index(self) -> ShotIndex:
        return parse_index(await self._get("/api/history/index.bin"))

    async def fetch_slog(self, shot_id: int) -> bytes:
        data = await self._get(f"/api/history/{shot_id:06d}.slog")
        if not is_slog(data):
            raise MachineError(f"shot {shot_id:06d}.slog missing (got SPA fallback)")
        return data

    async def fetch_notes(self, shot_id: int) -> dict | None:
        """Notes json, or None if absent. Tries padded then unpadded filename."""
        for name in (f"{shot_id:06d}.json", f"{shot_id}.json"):
            try:
                data = await self._get(f"/api/history/{name}")
            except aiohttp.ClientResponseError:
                continue
            text = data.lstrip()[:1]
            if text == b"{":
                with contextlib.suppress(json.JSONDecodeError):
                    return json.loads(data)
        return None

    async def fetch_status(self) -> dict:
        return json.loads(await self._get("/api/status"))

    async def fetch_settings(self, *, redact: bool = True) -> dict:
        settings = json.loads(await self._get("/api/settings"))
        if redact:
            for key in REDACTED_SETTINGS:
                if settings.get(key):
                    settings[key] = "<REDACTED>"
        return settings
