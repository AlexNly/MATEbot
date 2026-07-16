"""Audio-onset offset calibration."""

import asyncio
import math
import pathlib
import shutil
import struct

import pytest

from matebot import calibrate

FIXTURE = pathlib.Path(__file__).parent / "fixtures" / "000004.slog"


def pcm(silence_s: float, loud_s: float) -> bytes:
    sr = calibrate.SAMPLE_RATE
    quiet = [int(60 * math.sin(i / 7)) for i in range(int(silence_s * sr))]
    loud = [int(9000 * math.sin(i / 3)) for i in range(int(loud_s * sr))]
    return struct.pack(f"<{len(quiet) + len(loud)}h", *quiet, *loud)


def fake_ffmpeg(monkeypatch, data: bytes, rc: int = 0):
    class Proc:
        returncode = rc

        async def communicate(self):
            return data, b""

    async def fake_exec(*args, **kwargs):
        return Proc()

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_exec)


def test_audio_onset_finds_pump(monkeypatch, tmp_path):
    fake_ffmpeg(monkeypatch, pcm(4.1, 10))
    onset = asyncio.run(calibrate.audio_onset(tmp_path / "clip.mp4"))
    assert onset == pytest.approx(4.1, abs=0.15)


def test_audio_onset_none_when_quiet(monkeypatch, tmp_path):
    fake_ffmpeg(monkeypatch, pcm(20, 0))
    assert asyncio.run(calibrate.audio_onset(tmp_path / "clip.mp4")) is None


def test_pump_start_from_fixture():
    t = calibrate.pump_start(FIXTURE.read_bytes())
    assert t is not None and 0 <= t < 60


def test_calibrate_offset_end_to_end(monkeypatch, tmp_path):
    shots = tmp_path / "shots"
    shots.mkdir()
    shutil.copy(FIXTURE, shots / "000004.slog")
    (shots / "000004.mp4").write_bytes(b"clip")
    t_chart = calibrate.pump_start(FIXTURE.read_bytes())
    fake_ffmpeg(monkeypatch, pcm(t_chart + 0.6, 8))
    offset = asyncio.run(calibrate.calibrate_offset(tmp_path, 4))
    assert offset == pytest.approx(0.6, abs=0.15)


def test_calibrate_offset_missing_files(tmp_path):
    (tmp_path / "shots").mkdir()
    assert asyncio.run(calibrate.calibrate_offset(tmp_path, 4)) is None
