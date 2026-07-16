"""Shot reel rendering: ffmpeg arg construction, offset handling, fallbacks."""

import asyncio
import json
import pathlib
import shutil

import pytest

from matebot import render

FIXTURE = pathlib.Path(__file__).parent / "fixtures" / "000004.slog"
GEOM = {"img_w": 880, "img_h": 495, "t_end": 16.0, "x0": 93.0, "x1": 772.0,
        "y_top": 37.0, "plot_h": 358.0}


@pytest.fixture
def repo(tmp_path, monkeypatch):
    shots = tmp_path / "shots"
    shots.mkdir()
    shutil.copy(FIXTURE, shots / "000004.slog")
    (shots / "000004.mp4").write_bytes(b"fake clip")
    import matebot.plot as plot

    monkeypatch.setattr(plot, "render_shot_chart", lambda shot, title=None: (b"png", GEOM))
    return tmp_path


def run_capture(monkeypatch, results):
    calls = []

    async def fake_run(args):
        calls.append(args)
        rc, err = results[min(len(calls), len(results)) - 1]
        if rc == 0:
            pathlib.Path(args[-1]).write_bytes(b"reel")
        return rc, err

    monkeypatch.setattr(render, "_run", fake_run)
    return calls


def test_positive_offset_trims_clip(repo, monkeypatch):
    (repo / "shots" / "000004.video.json").write_text(json.dumps({"offset": 0.6}))
    calls = run_capture(monkeypatch, [(0, b"")])
    out = asyncio.run(render.render_reel(repo, 4))
    args = calls[0]
    assert args[args.index("-ss") + 1] == "0.60"
    assert args[args.index("-t") + 1] == f"{GEOM['t_end']:.2f}"  # ends at the x-axis end
    assert "adelay=0|0" in args[args.index("-filter_complex") + 1]
    assert out.read_bytes() == b"reel"
    out.unlink()


def test_negative_offset_delays_clip(repo, monkeypatch):
    (repo / "shots" / "000004.video.json").write_text(json.dumps({"offset": -1.0}))
    calls = run_capture(monkeypatch, [(0, b"")])
    out = asyncio.run(render.render_reel(repo, 4))
    fc = calls[0][calls[0].index("-filter_complex") + 1]
    assert "-ss" not in calls[0]
    assert "tpad=start_duration=1.00" in fc
    assert "adelay=1000|1000" in fc
    out.unlink()


def test_wipe_tracks_plot_area(repo, monkeypatch):
    calls = run_capture(monkeypatch, [(0, b"")])
    out = asyncio.run(render.render_reel(repo, 4))
    fc = calls[0][calls[0].index("-filter_complex") + 1]
    # x expression starts at the scaled axis origin, not the image edge
    left = GEOM["x0"] * render.WIDTH / GEOM["img_w"]
    assert f"{left:.1f}+" in fc
    assert f"t/{GEOM['t_end']}" in fc
    out.unlink()


def test_silent_retry_without_audio(repo, monkeypatch):
    calls = run_capture(monkeypatch, [(1, b"Stream map '[a]' matches no streams"), (0, b"")])
    out = asyncio.run(render.render_reel(repo, 4))
    assert len(calls) == 2
    assert "[a]" not in calls[1]
    assert "adelay" not in calls[1][calls[1].index("-filter_complex") + 1]
    out.unlink()


def test_missing_clip_raises(repo):
    (repo / "shots" / "000004.mp4").unlink()
    with pytest.raises(render.RenderError):
        asyncio.run(render.render_reel(repo, 4))
