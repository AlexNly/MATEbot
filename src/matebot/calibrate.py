"""Auto-calibrate the video/chart sync offset from the pump's audio onset.

A vibratory pump is loud. The moment it kicks in is visible in the shot data
(``tp`` — target pressure — leaves zero) and audible in the clip, so aligning
the two gives the exact offset with no manual ``/vsync`` needed:

    offset = t_pump_in_video - t_pump_in_chart

Pure python on 8 kHz mono PCM — no numpy. Returns ``None`` whenever the
signal is unclear; callers then keep the configured default.
"""

from __future__ import annotations

import asyncio
import logging
import struct
from pathlib import Path

from .slog import parse_slog

log = logging.getLogger(__name__)

SAMPLE_RATE = 8000
WINDOW_S = 0.05
SUSTAIN_WINDOWS = 6  # onset must stay loud for 0.3 s (a clink won't)


async def audio_onset(clip: Path) -> float | None:
    """Time (s) at which the clip's audio gets loud and stays loud."""
    try:
        proc = await asyncio.create_subprocess_exec(
            "ffmpeg", "-hide_banner", "-loglevel", "error", "-i", str(clip),
            "-vn", "-ac", "1", "-ar", str(SAMPLE_RATE), "-f", "s16le", "pipe:1",
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL,
        )
    except FileNotFoundError:
        return None
    pcm, _ = await proc.communicate()
    if proc.returncode != 0 or len(pcm) < SAMPLE_RATE:  # needs ≥0.5 s of audio
        return None
    n = len(pcm) // 2
    samples = struct.unpack(f"<{n}h", pcm[: n * 2])
    win = int(WINDOW_S * SAMPLE_RATE)
    rms = [
        (sum(x * x for x in samples[i : i + win]) / win) ** 0.5
        for i in range(0, n - win, win)
    ]
    if len(rms) < 30:
        return None
    head = sorted(rms[:20])
    base = head[len(head) // 2]  # median of the first second
    peak = max(rms)
    if peak < max(base, 1.0) * 4:  # no clear loud event (or mic muted)
        return None
    thresh = max(base * 5, peak * 0.25)
    for i in range(len(rms) - SUSTAIN_WINDOWS):
        if all(r > thresh for r in rms[i : i + SUSTAIN_WINDOWS]):
            return round(i * WINDOW_S, 2)
    return None


def pump_start(slog_bytes: bytes) -> float | None:
    """Time (s) at which the shot data first commands the pump."""
    shot = parse_slog(slog_bytes)
    step = shot.sample_interval_ms / 1000
    for key, thresh in (("tp", 0.1), ("fl", 0.3)):
        for i, v in enumerate(shot.series.get(key, [])):
            if v > thresh:
                return round(i * step, 2)
    return None


async def calibrate_offset(repo: str | Path, shot_id: int) -> float | None:
    """Offset for the shot's sidecar (video t = chart t + offset), or None."""
    repo = Path(repo)
    sid = f"{shot_id:06d}"
    clip = repo / "shots" / f"{sid}.mp4"
    slog = repo / "shots" / f"{sid}.slog"
    if not clip.exists() or not slog.exists():
        return None
    t_video = await audio_onset(clip)
    t_chart = pump_start(slog.read_bytes())
    if t_video is None or t_chart is None:
        return None
    offset = round(t_video - t_chart, 2)
    if not -5.0 <= offset <= 5.0:  # implausible match, don't trust it
        log.info("calibration rejected (offset %+.2fs out of range)", offset)
        return None
    return offset
