"""Independent protocol fixtures; no firmware source is included."""

import struct

import pytest


@pytest.fixture
def make_slog():
    def build(version=7, times=(0, 375, 70000), *, declared_size=None, mask=None):
        size = {5: 26, 6: 28, 7: 30}[version]
        header = bytearray(512)
        struct.pack_into('<IBBHHHIIII', header, 0, 0x544F4853, version,
                         size if declared_size is None else declared_size, 512, 250, 0,
                         mask if mask is not None else (0x3FFF if version == 7 else 0x1FFF),
                         len(times), 70000, 1700000000)
        header[60:67] = b'Classic'
        struct.pack_into('<H', header, 108, 360)
        struct.pack_into('<HB', header, 110, 1, 1)
        header[114:118] = b'Brew'
        header[458] = 1
        records = []
        for i, t in enumerate(times):
            # temperatures, pressures, flows, weights, resistance, system flags
            values = [930, 920, 0 if i == 0 else 90, 85, 0 if i == 0 else 200,
                      200, 180, -10, 100, 100, 100, 0]
            fmt = '<' + ('H' if version == 5 else 'I') + '4H4h4H'
            if version == 7:
                fmt += 'H'
                values.append(123 + i)
            records.append(struct.pack(fmt, t, *values))
        return bytes(header) + b''.join(records)
    return build
