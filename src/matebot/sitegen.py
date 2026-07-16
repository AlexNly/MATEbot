"""Generate the static shot-explorer site (GitHub Pages friendly).

Input:  a folder of ``NNNNNN.slog`` + optional ``NNNNNN.json`` notes files
        (the layout of a matebot/GaggiMate data repo's ``shots/`` dir).
Output: ``docs/`` with a self-contained viewer (no CDN):
        index.html + app.js + style.css + index.json + shots/<id>.json
"""

from __future__ import annotations

import importlib.resources
import json
import logging
import shutil
import subprocess
from pathlib import Path

from .slog import Shot, SlogError, parse_slog

log = logging.getLogger(__name__)

WEB_ASSETS = (
    "index.html",
    "app.js",
    "style.css",
    "vendor/chart.umd.js",
    "vendor/chartjs-plugin-annotation.min.js",
)


def _round(values: list[float], digits: int) -> list[float]:
    return [round(v, digits) for v in values]


def _shot_payload(shot: Shot, notes: dict | None) -> dict:
    s = shot.series
    step = shot.sample_interval_ms / 1000.0
    return {
        "header": {
            "profile": shot.profile_name,
            "ts": shot.start_epoch,
            "duration_s": round(shot.duration_ms / 1000, 1),
            "final_g": shot.final_weight_g,
            "phases": [
                {"t": round(p.sample_index * step, 2), "name": p.name} for p in shot.phases
            ],
        },
        "series": {
            "t": _round([t * step for t in s.get("t", [])], 2),
            "ct": _round(s.get("ct", []), 1),
            "tt": _round(s.get("tt", []), 1),
            "cp": _round(s.get("cp", []), 2),
            "tp": _round(s.get("tp", []), 2),
            "fl": _round(s.get("fl", []), 2),
            "tf": _round(s.get("tf", []), 2),
            "pf": _round(s.get("pf", []), 2),
            "vf": _round(s.get("vf", []), 2),
            "v": _round(s.get("v", []), 1),
            "ev": _round(s.get("ev", []), 1),
        },
        "notes": notes or {},
    }


def _load_notes(path: Path) -> dict | None:
    try:
        raw = path.read_bytes()
    except OSError:
        return None
    if not raw.lstrip().startswith(b"{"):  # gzip/HTML masquerade, see machine.py
        log.warning("skipping non-JSON notes file %s", path.name)
        return None
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return None


def _video_info(shots_dir: Path, out_dir: Path, sid: str) -> dict:
    """Copy the shot's video into the site (if any) and return payload fields."""
    mp4 = shots_dir / f"{sid}.mp4"
    if not mp4.exists():
        return {}
    dest = out_dir / "video" / f"{sid}.mp4"
    dest.parent.mkdir(parents=True, exist_ok=True)
    if not dest.exists() or dest.stat().st_mtime < mp4.stat().st_mtime:
        dest.write_bytes(mp4.read_bytes())
    offset = 0.0
    try:
        offset = float(json.loads((shots_dir / f"{sid}.video.json").read_text())["offset"])
    except (OSError, ValueError, KeyError):
        pass
    info = {"video": f"video/{sid}.mp4", "video_offset": offset}
    # poster frame: phones don't decode a frame until play, showing black
    poster = out_dir / "video" / f"{sid}.jpg"
    stale = not poster.exists() or poster.stat().st_mtime < mp4.stat().st_mtime
    if stale and shutil.which("ffmpeg"):
        proc = subprocess.run(
            ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-ss", "0.5",
             "-i", str(mp4), "-frames:v", "1", "-vf", "scale=640:-2", str(poster)],
            capture_output=True,
        )
        if proc.returncode != 0:
            poster.unlink(missing_ok=True)
    if poster.exists():
        info["poster"] = f"video/{sid}.jpg"
    return info


def generate(shots_dir: str | Path, out_dir: str | Path, *, title: str = "Shot Journal") -> int:
    """Build the site; returns the number of shots included."""
    shots_dir, out_dir = Path(shots_dir), Path(out_dir)
    (out_dir / "shots").mkdir(parents=True, exist_ok=True)

    index = []
    for slog_path in sorted(shots_dir.glob("*.slog")):
        try:
            shot = parse_slog(slog_path.read_bytes())
        except SlogError as exc:
            log.warning("skipping %s: %s", slog_path.name, exc)
            continue
        sid = slog_path.stem
        notes = _load_notes(slog_path.with_suffix(".json"))
        payload = _shot_payload(shot, notes)
        payload.update(_video_info(shots_dir, out_dir, sid))
        (out_dir / "shots" / f"{sid}.json").write_text(
            json.dumps(payload, separators=(",", ":"))
        )
        cp = payload["series"]["cp"]
        index.append(
            {
                "id": sid,
                "ts": shot.start_epoch,
                "duration_s": payload["header"]["duration_s"],
                "profile": shot.profile_name,
                "final_g": shot.final_weight_g,
                "peak_bar": max(cp) if cp else 0,
                "rating": (notes or {}).get("rating") or 0,
                "has_video": (shots_dir / f"{sid}.mp4").exists(),
                "bean": (notes or {}).get("beanType", ""),
                "ratio": (notes or {}).get("ratio", ""),
                "taste": (notes or {}).get("balanceTaste", ""),
            }
        )

    index.sort(key=lambda e: e["id"], reverse=True)
    (out_dir / "index.json").write_text(json.dumps({"title": title, "shots": index}))

    web = importlib.resources.files("matebot") / "web"
    (out_dir / "vendor").mkdir(exist_ok=True)
    for name in WEB_ASSETS:
        (out_dir / name).write_text((web / name).read_text())
    log.info("site generated: %d shots -> %s", len(index), out_dir)
    return len(index)
