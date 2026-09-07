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


@pytest.mark.parametrize('version', [5, 6, 7])
def test_plot_uses_recorded_timeline(make_slog, version, monkeypatch):
    from matplotlib.axes import Axes

    from matebot.plot import render_shot_chart

    verticals = []
    original = Axes.axvline

    def capture(self, x=0, *args, **kwargs):
        verticals.append(x)
        return original(self, x, *args, **kwargs)

    monkeypatch.setattr(Axes, 'axvline', capture)
    times = (0, 2, 280) if version == 5 else (0, 375, 70000)
    png, geometry = render_shot_chart(parse_slog(make_slog(version, times)))
    assert png.startswith(b'\x89PNG')
    assert geometry['t_end'] == 70
    assert verticals == ([.5] if version == 5 else [.375])
