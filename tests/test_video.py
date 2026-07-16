import json
import pathlib
import shutil

import pytest

from matebot import video

FIXTURES = pathlib.Path(__file__).parent / "fixtures"


class FakeProc:
    def __init__(self, returncode=0):
        self.returncode = returncode

    async def communicate(self):
        return b"", None


@pytest.mark.asyncio
async def test_attach_video_transcodes_and_writes_sidecar(tmp_path, monkeypatch):
    async def fake_exec(*argv, **kw):
        # ffmpeg is mocked: "transcode" by writing the output path (last arg)
        pathlib.Path(argv[-1]).write_bytes(b"MP4!")
        return FakeProc(0)

    monkeypatch.setattr(video.asyncio, "create_subprocess_exec", fake_exec)
    out = await video.attach_video(tmp_path, 77, tmp_path / "in.webm", offset=-1.2)
    assert out == tmp_path / "shots" / "000077.mp4"
    assert out.read_bytes() == b"MP4!"
    assert json.loads(video.sidecar_path(tmp_path, 77).read_text()) == {"offset": -1.2}
    assert video.get_offset(tmp_path, 77) == -1.2
    assert video.latest_video_shot(tmp_path) == 77


@pytest.mark.asyncio
async def test_attach_video_ffmpeg_failure(tmp_path, monkeypatch):
    async def fake_exec(*argv, **kw):
        return FakeProc(1)

    monkeypatch.setattr(video.asyncio, "create_subprocess_exec", fake_exec)
    with pytest.raises(video.VideoError):
        await video.attach_video(tmp_path, 78, tmp_path / "in.webm")
    assert video.get_offset(tmp_path, 78) is None  # no partial artifacts


def test_get_offset_defaults_and_absence(tmp_path):
    assert video.get_offset(tmp_path, 5) is None  # no video at all
    (tmp_path / "shots").mkdir()
    (tmp_path / "shots" / "000005.mp4").write_bytes(b"x")
    assert video.get_offset(tmp_path, 5) == 0.0  # video without sidecar


def test_prune_keeps_newest(tmp_path):
    shots = tmp_path / "shots"
    docs = tmp_path / "docs" / "video"
    shots.mkdir(parents=True)
    docs.mkdir(parents=True)
    for sid in range(1, 6):
        (shots / f"{sid:06d}.mp4").write_bytes(b"v")
        (shots / f"{sid:06d}.video.json").write_text("{}")
        (docs / f"{sid:06d}.mp4").write_bytes(b"v")
    removed = video.prune_videos(tmp_path, keep=2)
    assert removed == 3
    assert sorted(p.name for p in shots.glob("*.mp4")) == ["000004.mp4", "000005.mp4"]
    assert sorted(p.name for p in docs.glob("*.mp4")) == ["000004.mp4", "000005.mp4"]
    assert video.prune_videos(tmp_path, keep=0) == 0  # 0 = keep everything


def test_sitegen_includes_video(tmp_path):
    from matebot.sitegen import generate

    shots = tmp_path / "shots"
    shots.mkdir()
    shutil.copy(FIXTURES / "000004.slog", shots / "000004.slog")
    (shots / "000004.mp4").write_bytes(b"MP4DATA")
    (shots / "000004.video.json").write_text('{"offset": -0.8}')
    out = tmp_path / "docs"
    generate(shots, out, title="T")

    payload = json.loads((out / "shots" / "000004.json").read_text())
    assert payload["video"] == "video/000004.mp4"
    assert payload["video_offset"] == -0.8
    assert (out / "video" / "000004.mp4").read_bytes() == b"MP4DATA"
    index = json.loads((out / "index.json").read_text())
    assert index["shots"][0]["has_video"] is True
