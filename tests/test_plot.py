import pathlib

import pytest

pytest.importorskip("matplotlib")

from matebot.plot import render_shot_png  # noqa: E402
from matebot.slog import parse_slog  # noqa: E402

GOLDEN = pathlib.Path(__file__).parent / "fixtures" / "000004.slog"


def test_render_png_from_golden_shot():
    shot = parse_slog(GOLDEN.read_bytes())
    png = render_shot_png(shot, title="Shot #4 — Adaptive v2")
    assert png[:8] == b"\x89PNG\r\n\x1a\n"
    assert len(png) > 20_000  # an actual chart, not an empty canvas
