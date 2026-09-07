"""Shot-end detection over the GaggiMate status stream.

A shot "ends" when ``process.a`` transitions 1 -> 0 (or process becomes null)
while the machine was in brew mode. The finished shot's id is then resolved by polling ``index.bin``
(the header/index are finalized only after extended recording ends, which can
take up to ~1 minute after the pump stops while a bluetooth scale settles).
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
from collections.abc import AsyncIterator
from dataclasses import dataclass
from pathlib import Path

from .machine import GaggiMateClient
from .slog import IndexEntry
from .status import DISCONNECTED, StatusAccumulator

log = logging.getLogger(__name__)

MODE_BREW = 1
BREW_PROCESSES = {"brew", "infusion"}


@dataclass
class FinishedShot:
    entry: IndexEntry
    profile_label: str
    duration_ms: int


class ShotWatcher:
    def __init__(
        self,
        client: GaggiMateClient,
        *,
        min_duration_s: float = 10.0,
        ignore_profiles: str = r"(?i)backflush|descale|flush|clean",
        last_known_id: int = -1,
        on_utility=None,
    ) -> None:
        self.client = client
        self.min_duration_ms = min_duration_s * 1000
        self.ignore_re = re.compile(ignore_profiles) if ignore_profiles else None
        self.last_known_id = last_known_id
        self.on_utility = on_utility  # async callback(profile) for ignored utility runs

    def _frame_is_shot_end(self, prev: dict | None, frame: dict) -> bool:
        if prev is None:
            return False
        p_prev = prev.get("process") or {}
        p_now = frame.get("process") or {}
        return (
            p_prev.get("a") == 1
            and "process" in frame
            and (frame["process"] is None or p_now.get("a") == 0)
            and prev.get("m") == MODE_BREW
            and p_prev.get("s", "brew") in BREW_PROCESSES
        )

    def _accept(self, profile: str, duration_ms: float) -> bool:
        if duration_ms < self.min_duration_ms:
            log.info("ignoring short shot (%.1fs)", duration_ms / 1000)
            return False
        if self.ignore_re and self.ignore_re.search(profile or ""):
            log.info("ignoring utility profile %r", profile)
            return False
        return True

    async def _resolve_new_entry(
        self, *, duration_hint_ms: int = 0, budget_s: float = 120.0, poll_s: float = 3.0
    ):
        """Poll index.bin for the entry belonging to the shot that just ended.

        The entry is only flagged completed after extended recording (up to
        ~1 min of scale settling), while older completed-but-unclaimed
        entries (e.g. an aborted shot minutes earlier) are already sitting in
        the index. Picking "newest completed" therefore resolves one shot
        behind — so a candidate must also match the observed duration. Only
        when the budget runs out do we fall back to the newest completed
        entry, loudly.
        """
        waited = 0.0
        fallback = None
        while waited <= budget_s:
            try:
                index = await self.client.fetch_index()
            except Exception as exc:  # noqa: BLE001 - transient HTTP errors are fine
                log.debug("index poll failed: %s", exc)
            else:
                fresh = [
                    e
                    for e in index.entries
                    if e.id > self.last_known_id and e.completed and not e.deleted
                ]
                for entry in sorted(fresh, key=lambda e: e.id, reverse=True):
                    if (
                        not duration_hint_ms
                        or abs(entry.duration_ms - duration_hint_ms) <= 10_000
                    ):
                        return entry
                if fresh:
                    fallback = max(fresh, key=lambda e: e.id)
            await asyncio.sleep(poll_s)
            waited += poll_s
        if fallback is not None:
            log.warning(
                "no index entry matched the observed %.1fs shot; falling back to id %d (%.1fs)",
                duration_hint_ms / 1000, fallback.id, fallback.duration_ms / 1000,
            )
        return fallback

    async def shots(self, frames: AsyncIterator[dict]) -> AsyncIterator[FinishedShot]:
        """Consume merged status snapshots, yield finished (accepted) shots."""
        prev: dict | None = None
        # Initialize last_known_id from the machine so pre-existing shots
        # never fire the questionnaire.
        if self.last_known_id < 0:
            try:
                index = await self.client.fetch_index()
                self.last_known_id = max((e.id for e in index.entries), default=0)
                log.info("starting after shot id %d", self.last_known_id)
            except Exception:  # noqa: BLE001
                self.last_known_id = 0

        async for frame in frames:
            if frame.get("tp") == DISCONNECTED:
                prev = None
                continue
            if frame.get("tp") != "evt:status":
                continue
            if self._frame_is_shot_end(prev, frame):
                profile = prev.get("p", "")
                duration = (prev.get("process") or {}).get("e", 0)
                log.info("shot ended: profile=%r duration=%.1fs", profile, duration / 1000)
                is_utility = (
                    duration >= self.min_duration_ms
                    and self.ignore_re
                    and self.ignore_re.search(profile or "")
                )
                if is_utility and self.on_utility:
                    await self.on_utility(profile)
                if self._accept(profile, duration) and not (prev.get("process") or {}).get("u"):
                    entry = await self._resolve_new_entry(duration_hint_ms=int(duration))
                    if entry is None:
                        log.warning("shot ended but no new index entry appeared")
                    else:
                        log.info(
                            "resolved shot id=%d (%.1fs, observed %.1fs)",
                            entry.id, entry.duration_ms / 1000, duration / 1000,
                        )
                        self.last_known_id = entry.id
                        yield FinishedShot(entry, profile, entry.duration_ms or duration)
            if (frame.get("process") or {}).get("a") == 1 and prev is not None:
                if (prev.get("process") or {}).get("a") == 1 and prev.get("m") is not None:
                    # A new selection/mode is slow state, not the identity of
                    # the brew already in progress. Preserve its original context.
                    frame = {**frame, "m": prev.get("m"), "p": prev.get("p", "")}
            prev = frame


async def replay_frames(path: str | Path, *, delay_s: float = 0.0) -> AsyncIterator[dict]:
    """Replay a JSONL capture of WS frames (for tests and --replay dry-runs)."""
    status = StatusAccumulator()
    for line in Path(path).read_text().splitlines():
        line = line.strip()
        if not line:
            continue
        if delay_s:
            await asyncio.sleep(delay_s)
        yield status.apply(json.loads(line))
