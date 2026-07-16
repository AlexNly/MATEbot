"""Shot video handling: transcode, sidecar metadata, retention pruning.

Videos live next to the shot files as ``shots/NNNNNN.mp4`` with a sidecar
``shots/NNNNNN.video.json`` (currently ``{"offset": seconds}`` — where shot
t=0 sits relative to video t=0). The sync pipeline copies them into
``docs/video/`` for GitHub Pages and prunes to the newest N so the repo
doesn't grow unbounded (~3 MB per clip).
"""

from __future__ import annotations

import asyncio
import json
import logging
from pathlib import Path

log = logging.getLogger(__name__)

FFMPEG_ARGS = [
    "-c:v", "libx264", "-preset", "veryfast", "-crf", "26",
    "-vf", "scale='min(1280,iw)':-2",
    "-c:a", "aac", "-b:a", "96k",
    "-movflags", "+faststart",
]


class VideoError(RuntimeError):
    pass


async def attach_video(
    repo: str | Path, shot_id: int, source: str | Path, *, offset: float = 0.0
) -> Path:
    """Transcode *source* into the repo as the shot's video; returns the mp4 path."""
    repo = Path(repo)
    out = repo / "shots" / f"{shot_id:06d}.mp4"
    out.parent.mkdir(parents=True, exist_ok=True)
    proc = await asyncio.create_subprocess_exec(
        "ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
        "-i", str(source), *FFMPEG_ARGS, str(out),
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT,
    )
    stdout, _ = await proc.communicate()
    if proc.returncode != 0 or not out.exists() or out.stat().st_size == 0:
        out.unlink(missing_ok=True)
        raise VideoError(f"ffmpeg failed ({proc.returncode}): {stdout.decode()[-300:]}")
    set_offset(repo, shot_id, offset)
    log.info("video attached to shot %06d (%.1f MB)", shot_id, out.stat().st_size / 1e6)
    return out


def sidecar_path(repo: str | Path, shot_id: int) -> Path:
    return Path(repo) / "shots" / f"{shot_id:06d}.video.json"


def set_offset(repo: str | Path, shot_id: int, offset: float) -> None:
    sidecar_path(repo, shot_id).write_text(json.dumps({"offset": round(offset, 2)}))


def get_offset(repo: str | Path, shot_id: int) -> float | None:
    """Offset for the shot's video, or None if the shot has no video."""
    if not (Path(repo) / "shots" / f"{shot_id:06d}.mp4").exists():
        return None
    try:
        return float(json.loads(sidecar_path(repo, shot_id).read_text())["offset"])
    except (OSError, ValueError, KeyError):
        return 0.0


def latest_video_shot(repo: str | Path) -> int | None:
    videos = sorted((Path(repo) / "shots").glob("[0-9]*.mp4"))
    return int(videos[-1].stem) if videos else None


def prune_videos(repo: str | Path, keep: int) -> int:
    """Delete all but the newest *keep* videos (shots/ and docs/video/); returns count removed."""
    repo = Path(repo)
    videos = sorted((repo / "shots").glob("[0-9]*.mp4"))
    removed = 0
    for mp4 in videos[:-keep] if keep > 0 else []:
        shot = mp4.stem
        mp4.unlink(missing_ok=True)
        (repo / "shots" / f"{shot}.video.json").unlink(missing_ok=True)
        (repo / "docs" / "video" / f"{shot}.mp4").unlink(missing_ok=True)
        removed += 1
    if removed:
        log.info("pruned %d old shot videos (keeping %d)", removed, keep)
    return removed
