import gzip
import pathlib
import struct

import pytest

from gaggibot.slog import SlogError, is_slog, parse_index, parse_slog

FIXTURES = pathlib.Path(__file__).parent / "fixtures"
GOLDEN = FIXTURES / "000004.slog"


def test_golden_header():
    shot = parse_slog(GOLDEN.read_bytes())
    assert shot.version == 5
    assert shot.sample_interval_ms == 250
    assert shot.fields_mask == 0x1FFF
    assert shot.sample_count == 215
    assert shot.duration_ms == 53876
    assert shot.profile_name == "Adaptive v2"
    assert shot.final_weight_g == pytest.approx(48.2)


def test_golden_phases():
    shot = parse_slog(GOLDEN.read_bytes())
    assert shot.phases[0].name == "Prefill"
    assert shot.phases[0].sample_index == 0
    assert shot.phases[1].name == "Fill"
    assert shot.phases[1].sample_index == 20


def test_golden_series():
    shot = parse_slog(GOLDEN.read_bytes())
    for key in ("t", "ct", "tt", "cp", "tp", "fl", "v"):
        assert len(shot.series[key]) == 215
    # ticks are monotonically non-decreasing; times derive from interval
    assert shot.series["t"] == sorted(shot.series["t"])
    assert shot.times_s[1] - shot.times_s[0] == pytest.approx(0.25)
    # plausibility: espresso temps and pressures
    assert 15 < max(shot.series["ct"]) < 120
    assert 0 <= max(shot.series["cp"]) < 16


def test_csv_roundtrip():
    shot = parse_slog(GOLDEN.read_bytes())
    lines = shot.to_csv().strip().splitlines()
    assert len(lines) == 1 + shot.sample_count
    assert lines[0].startswith("time_s,t,tt,ct")


def test_truncated_sample_section_is_tolerated():
    data = GOLDEN.read_bytes()
    cut = parse_slog(data[: 512 + 26 * 10 + 13])  # 10 complete samples + garbage tail
    assert cut.sample_count == 10
    assert len(cut.series["ct"]) == 10


def test_rejects_gzip_html_masquerade():
    # the ESP32 returns gzipped index.html with HTTP 200 for missing files
    fake = gzip.compress(b"<!doctype html><html>...</html>")
    assert not is_slog(fake)
    with pytest.raises(SlogError):
        parse_slog(fake)


def test_index_parse_synthetic():
    header = struct.pack("<IHHII16x", 0x58444953, 1, 128, 2, 7)
    def entry(sid, flags, name):
        return struct.pack(
            "<IIIHBB32s48s32x", sid, 1700000000, 30000, 361, 4, flags, b"prof", name
        )
    blob = header + entry(5, 0x01, b"Adaptive v2") + entry(6, 0x01 | 0x04, b"Classic")
    idx = parse_index(blob)
    assert idx.next_id == 7
    assert [e.id for e in idx.entries] == [5, 6]
    assert idx.entries[0].completed and not idx.entries[0].has_notes
    assert idx.entries[1].has_notes
    assert idx.entries[0].volume_g == pytest.approx(36.1)
    assert idx.entries[0].padded_id == "000005"
    assert idx.entries[1].profile_name == "Classic"
