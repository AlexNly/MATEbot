import gzip
import pathlib
import struct

import pytest

from matebot.slog import SlogError, is_slog, parse_index, parse_slog

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


@pytest.mark.parametrize('version', [5, 6, 7])
def test_versioned_samples(make_slog, version):
    times = (0, 1, 280) if version == 5 else (0, 375, 70000)
    shot = parse_slog(make_slog(version, times))
    assert shot.times_s == ([0, .25, 70] if version == 5 else [0, .375, 70])
    assert shot.series['ct'] == [92, 92, 92]
    assert shot.series['cp'] == [8.5, 8.5, 8.5]
    assert shot.series['vf'] == [-.1, -.1, -.1]
    assert shot.series.get('wp') == ([12.3, 12.4, 12.5] if version == 7 else None)
    assert shot.phase_times_s == [(.25 if version == 5 else .375, 'Brew')]
    assert float(shot.to_csv().splitlines()[2].split(',')[0]) == shot.times_s[1]
    assert ('wp' in shot.to_dict()['series']) == (version == 7)


@pytest.mark.parametrize('version', [5, 6, 7])
def test_unset_record_size_and_truncation(make_slog, version):
    times = (0, 1, 2) if version == 5 else (0, 375, 70000)
    blob = make_slog(version, times, declared_size=0)
    shot = parse_slog(blob[:-1])
    assert shot.sample_count == 2
    assert shot.series['ct'] == [92, 92]
    # A phase pointing beyond the recovered data is omitted.
    header = bytearray(blob[:512])
    struct.pack_into('<H', header, 110, 2)
    assert parse_slog(bytes(header) + blob[512:-1]).phase_times_s == []


@pytest.mark.parametrize('change', ['version', 'size', 'mask', 'no_time', 'short_header'])
def test_reject_unsupported_layout(make_slog, change):
    data = bytearray(make_slog())
    if change == 'version':
        data[4] = 8
    elif change == 'size':
        data[5] = 26
    elif change == 'mask':
        struct.pack_into('<I', data, 12, 0x7FFF)
    elif change == 'no_time':
        struct.pack_into('<I', data, 12, 0x3FFE)
    else:
        data = data[:25]
    with pytest.raises(SlogError):
        parse_slog(bytes(data))


@pytest.mark.parametrize('version', [5, 6, 7])
def test_sparse_field_mask_controls_offsets(make_slog, version):
    data = bytearray(make_slog(version, (0, 1, 2))[:512])
    # Timestamp, current temperature, and (in v7) cumulative pumped water.
    mask = 0x5 | (0x2000 if version == 7 else 0)
    fmt = ('<HH' if version == 5 else '<IH') + ('H' if version == 7 else '')
    data[5] = struct.calcsize(fmt)
    struct.pack_into('<II', data, 12, mask, 2)
    body = b''.join(struct.pack(fmt, t, 920, *([125] if version == 7 else []))
                    for t in (0, 1 if version == 5 else 70000))
    shot = parse_slog(bytes(data) + body)
    assert shot.series['ct'] == [92, 92]
    assert shot.times_s == ([0, .25] if version == 5 else [0, 70])
    assert 'cp' not in shot.series
