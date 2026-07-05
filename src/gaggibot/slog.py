"""Decoder for GaggiMate binary shot logs (``.slog``) and the shot index (``index.bin``).

Format reference: ``src/display/models/shot_log_format.h`` in the GaggiMate
firmware (format v5, little-endian throughout). Older versions carry fewer
sample fields; ``fieldsMask`` says which are present, always in the fixed
order below.
"""

from __future__ import annotations

import csv
import io
import json
import struct
from dataclasses import dataclass, field

SHOT_MAGIC = 0x544F4853  # "SHOT"
INDEX_MAGIC = 0x58444953  # "SIDX"

# (key, struct char, divisor) in fieldsMask bit order 0..12.
SAMPLE_FIELDS = [
    ("t", "H", None),  # sample tick; seconds = t * sampleInterval / 1000
    ("tt", "H", 10),  # target temp °C
    ("ct", "H", 10),  # current temp °C
    ("tp", "H", 10),  # target pressure bar
    ("cp", "H", 10),  # current pressure bar
    ("fl", "h", 100),  # pump flow ml/s
    ("tf", "h", 100),  # target flow ml/s
    ("pf", "h", 100),  # puck flow ml/s
    ("vf", "h", 100),  # bluetooth-scale flow ml/s
    ("v", "H", 10),  # bluetooth weight g
    ("ev", "H", 10),  # estimated weight g
    ("pr", "H", 100),  # puck resistance
    ("si", "H", None),  # system info bitfield
]

# ShotIndexEntry.flags
FLAG_COMPLETED = 0x01
FLAG_DELETED = 0x02
FLAG_HAS_NOTES = 0x04


class SlogError(ValueError):
    """Raised when input bytes are not a valid shot log / index."""


def _cstr(raw: bytes) -> str:
    return raw.split(b"\0", 1)[0].decode("utf-8", errors="replace")


@dataclass
class PhaseTransition:
    sample_index: int
    phase_number: int
    name: str


@dataclass
class Shot:
    version: int
    sample_interval_ms: int
    fields_mask: int
    sample_count: int
    duration_ms: int
    start_epoch: int
    profile_id: str
    profile_name: str
    final_weight_g: float
    phases: list[PhaseTransition] = field(default_factory=list)
    # column-oriented: {"t": [...], "ct": [...], ...} only for fields present in fields_mask
    series: dict[str, list[float]] = field(default_factory=dict)

    @property
    def times_s(self) -> list[float]:
        step = self.sample_interval_ms / 1000.0
        return [t * step for t in self.series.get("t", [])]

    def to_dict(self) -> dict:
        return {
            "version": self.version,
            "sample_interval_ms": self.sample_interval_ms,
            "sample_count": self.sample_count,
            "duration_ms": self.duration_ms,
            "start_epoch": self.start_epoch,
            "profile_id": self.profile_id,
            "profile_name": self.profile_name,
            "final_weight_g": self.final_weight_g,
            "phases": [
                {"sample_index": p.sample_index, "phase": p.phase_number, "name": p.name}
                for p in self.phases
            ],
            "series": self.series,
        }

    def to_json(self, **kwargs) -> str:
        return json.dumps(self.to_dict(), **kwargs)

    def to_csv(self) -> str:
        keys = [k for k, _, _ in SAMPLE_FIELDS if k in self.series]
        out = io.StringIO()
        w = csv.writer(out)
        w.writerow(["time_s", *keys])
        step = self.sample_interval_ms / 1000.0
        for i in range(len(self.series.get("t", []))):
            w.writerow([self.series["t"][i] * step, *(self.series[k][i] for k in keys)])
        return out.getvalue()


@dataclass
class IndexEntry:
    id: int
    timestamp: int
    duration_ms: int
    volume_g: float
    rating: int
    flags: int
    profile_id: str
    profile_name: str

    @property
    def completed(self) -> bool:
        return bool(self.flags & FLAG_COMPLETED)

    @property
    def deleted(self) -> bool:
        return bool(self.flags & FLAG_DELETED)

    @property
    def has_notes(self) -> bool:
        return bool(self.flags & FLAG_HAS_NOTES)

    @property
    def padded_id(self) -> str:
        return f"{self.id:06d}"


@dataclass
class ShotIndex:
    version: int
    next_id: int
    entries: list[IndexEntry]


def parse_slog(data: bytes) -> Shot:
    """Parse a .slog file. Raises SlogError on anything that isn't one.

    Tolerates truncated sample sections (crash during recording): decodes as
    many complete samples as are actually present.
    """
    if len(data) < 24 or struct.unpack_from("<I", data, 0)[0] != SHOT_MAGIC:
        raise SlogError("not a shot log (missing SHOT magic)")

    version, _sample_size, header_size, interval, _res1 = struct.unpack_from("<BBHHH", data, 4)
    fields_mask, sample_count, duration_ms, start_epoch = struct.unpack_from("<IIII", data, 12)
    if header_size < 108 or header_size > len(data):
        raise SlogError(f"implausible headerSize {header_size}")
    profile_id = _cstr(data[28:60])
    profile_name = _cstr(data[60:108])
    (final_weight,) = struct.unpack_from("<H", data, 108)

    phases: list[PhaseTransition] = []
    if version >= 5 and header_size >= 459:
        count = data[458]
        for i in range(min(count, 12)):
            off = 110 + i * 29
            idx, num = struct.unpack_from("<HB", data, off)
            phases.append(PhaseTransition(idx, num, _cstr(data[off + 4 : off + 29])))

    present = [(k, c, d) for bit, (k, c, d) in enumerate(SAMPLE_FIELDS) if fields_mask & (1 << bit)]
    fmt = "<" + "".join(c for _, c, _ in present)
    size = struct.calcsize(fmt)
    body = data[header_size:]
    n_available = len(body) // size if size else 0
    n = min(sample_count, n_available) if sample_count else n_available

    series: dict[str, list[float]] = {k: [] for k, _, _ in present}
    for values in struct.iter_unpack(fmt, body[: n * size]):
        for (k, _, div), v in zip(present, values, strict=True):
            series[k].append(v / div if div else v)

    return Shot(
        version=version,
        sample_interval_ms=interval or 250,
        fields_mask=fields_mask,
        sample_count=n,
        duration_ms=duration_ms,
        start_epoch=start_epoch,
        profile_id=profile_id,
        profile_name=profile_name,
        final_weight_g=final_weight / 10.0,
        phases=phases,
        series=series,
    )


def parse_index(data: bytes) -> ShotIndex:
    """Parse /h/index.bin."""
    if len(data) < 32 or struct.unpack_from("<I", data, 0)[0] != INDEX_MAGIC:
        raise SlogError("not a shot index (missing SIDX magic)")
    version, entry_size, entry_count, next_id = struct.unpack_from("<HHII", data, 4)
    if entry_size < 96:
        raise SlogError(f"implausible entrySize {entry_size}")
    entries = []
    for i in range(entry_count):
        off = 32 + i * entry_size
        if off + entry_size > len(data):
            break  # truncated index
        sid, ts, dur, vol, rating, flags = struct.unpack_from("<IIIHBB", data, off)
        entries.append(
            IndexEntry(
                id=sid,
                timestamp=ts,
                duration_ms=dur,
                volume_g=vol / 10.0,
                rating=rating,
                flags=flags,
                profile_id=_cstr(data[off + 16 : off + 48]),
                profile_name=_cstr(data[off + 48 : off + 96]),
            )
        )
    return ShotIndex(version=version, next_id=next_id, entries=entries)


def is_slog(data: bytes) -> bool:
    return len(data) >= 4 and struct.unpack_from("<I", data, 0)[0] == SHOT_MAGIC
