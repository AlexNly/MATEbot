"""Render a shareable "shot reel": camera clip on top, animated chart below.

The chart is drawn once (``plot.render_shot_chart``) and animated purely in
ffmpeg: a semi-transparent panel wipes across the data area in sync with the
shot while a playhead bar tracks the x axis (the axis pixel geometry comes
from matplotlib, so the playhead crosses "8" exactly at t=8). The clip is
delayed or trimmed by the sidecar offset, giving sync by construction.
Portrait layout, made for messengers.

Requires ffmpeg and the ``plots`` extra (matplotlib); callers should treat
``RenderError`` as "no reel this time", never as fatal.
"""

from __future__ import annotations

import asyncio
import logging
import tempfile
from pathlib import Path

from .slog import parse_slog
from .video import get_offset

log = logging.getLogger(__name__)

WIDTH = 720
CHART_H = 406
PLAYHEAD = "0xF0561D"  # GaggiMate orange
TAIL_S = 2.0


class RenderError(Exception):
    """Reel could not be rendered (missing deps, data or ffmpeg failure)."""


async def render_reel(repo: str | Path, shot_id: int, *, title: str | None = None) -> Path:
    """Compose ``shots/NNNNNN.mp4`` + chart into a reel; returns a temp mp4 path.

    The caller owns (and should unlink) the returned file.
    """
    repo = Path(repo)
    sid = f"{shot_id:06d}"
    clip = repo / "shots" / f"{sid}.mp4"
    slog = repo / "shots" / f"{sid}.slog"
    if not clip.exists():
        raise RenderError(f"no clip for shot {sid}")
    if not slog.exists():
        raise RenderError(f"no shot data for {sid}")

    shot = parse_slog(slog.read_bytes())
    try:
        from .plot import render_shot_chart
    except ImportError as exc:  # pragma: no cover - import always works, mpl may not
        raise RenderError("the plots extra (matplotlib) is not installed") from exc
    try:
        chart_png, geom = render_shot_chart(shot, title=title or f"Shot #{shot_id}")
    except ImportError as exc:
        raise RenderError("the plots extra (matplotlib) is not installed") from exc

    offset = get_offset(repo, shot_id) or 0.0
    delay = max(0.0, -offset)  # video starts this long after chart t=0
    trim = max(0.0, offset)  # positive offset: drop the clip's head instead
    dur = geom["t_end"]  # animate across the actual data range
    total = dur + TAIL_S

    # scale axis geometry from the rendered PNG to the reel's chart panel
    sx, sy = WIDTH / geom["img_w"], CHART_H / geom["img_h"]
    left = geom["x0"] * sx
    span = (geom["x1"] - geom["x0"]) * sx
    y_top = int(geom["y_top"] * sy)
    plot_h = max(2, int(geom["plot_h"] * sy) // 2 * 2)

    with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as f:
        f.write(chart_png)
        chart_path = Path(f.name)
    out = Path(tempfile.mkstemp(suffix=".mp4")[1])

    vid_in = ["-i", str(clip)] if trim == 0 else ["-ss", f"{trim:.2f}", "-i", str(clip)]
    delay_ms = int(delay * 1000)
    x_expr = f"{left:.1f}+{span:.1f}*min(1\\,t/{dur})"
    fc = (
        f"[0:v]tpad=start_duration={delay:.2f}:start_mode=clone,"
        f"scale={WIDTH}:-2,fps=30[vid];"
        f"[1:v]scale={WIDTH}:{CHART_H},fps=30,format=rgba[ch];"
        f"[ch][2:v]overlay=x='{x_expr}':y={y_top}[rev];"
        f"[rev][3:v]overlay=x='{x_expr}':y={y_top}:enable='lte(t,{dur})'[chart];"
        f"[vid][chart]vstack=inputs=2,format=yuv420p[v];"
        f"[0:a]adelay={delay_ms}|{delay_ms}[a]"
    )
    args = [
        "ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
        *vid_in,
        "-loop", "1", "-i", str(chart_path),
        "-f", "lavfi", "-i", f"color=c=white@0.88:s={int(span)}x{plot_h},format=rgba",
        "-f", "lavfi", "-i", f"color=c={PLAYHEAD}:s=3x{plot_h}",
        "-filter_complex", fc,
        "-map", "[v]", "-map", "[a]", "-t", f"{total:.2f}", "-r", "30",
        "-c:v", "libx264", "-preset", "veryfast", "-crf", "25",
        "-c:a", "aac", "-b:a", "128k", "-movflags", "+faststart",
        str(out),
    ]
    try:
        rc, err = await _run(args)
        if rc != 0:
            # most likely a clip without an audio track: retry silent once
            silent = list(args)
            silent[silent.index(fc)] = fc.rsplit(";", 1)[0]
            j = silent.index("[a]")
            del silent[j - 1 : j + 1]  # drop -map [a]
            rc, err = await _run(silent)
        if rc != 0:
            raise RenderError(f"ffmpeg failed: {err.decode(errors='replace')[-300:]}")
    except FileNotFoundError as exc:
        raise RenderError("ffmpeg is not installed") from exc
    finally:
        chart_path.unlink(missing_ok=True)
        if not out.exists() or out.stat().st_size == 0:
            out.unlink(missing_ok=True)
    if not out.exists():
        raise RenderError("ffmpeg produced no output")
    log.info("reel rendered for shot %s (%.1f MB)", sid, out.stat().st_size / 1e6)
    return out


async def _run(args: list[str]) -> tuple[int, bytes]:
    proc = await asyncio.create_subprocess_exec(
        *args, stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.PIPE
    )
    _, err = await proc.communicate()
    return proc.returncode or 0, err or b""
