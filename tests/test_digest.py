from datetime import datetime

from gaggibot.digest import compute, seconds_until_sunday_evening

NOW = datetime(2026, 7, 5, 20, 0)  # a Sunday evening
TS = NOW.timestamp()


def row(sid, days_ago=1, rating=0, volume=36.0, profile="Direct Lever v3", dur=30000):
    return {
        "id": sid, "ts": TS - days_ago * 86400, "duration_ms": dur,
        "volume_g": volume, "rating": rating, "profile": profile,
    }


def test_empty_week():
    assert compute([row(1, days_ago=10)], now=NOW) is None
    assert compute([], now=NOW) is None


def test_digest_content():
    rows = [
        row(1, days_ago=10, rating=5),           # too old
        row(2, days_ago=2, rating=4),
        row(3, days_ago=1, rating=5),
        row(4, days_ago=1),                       # unrated still counts
        row(5, days_ago=3, profile="Descale Flush Brewhead"),  # utility: excluded
        row(6, days_ago=1, dur=5000),             # too short: excluded
    ]
    text = compute(rows, now=NOW, journal_url="https://x.github.io/j/")
    assert "3 shots" in text
    assert "108 g" in text
    assert "4.5★ (2 rated)" in text
    assert "Best shot: #3" in text and "#000003" in text


def test_seconds_until_sunday():
    # Sunday 20:00 -> next Sunday 18:00 is ~6.9 days away
    s = seconds_until_sunday_evening(NOW)
    assert 6 * 86400 < s < 7 * 86400
    # Friday noon -> Sunday 18:00 is 2 days + 6 h
    friday = datetime(2026, 7, 3, 12, 0)
    assert seconds_until_sunday_evening(friday) == 2 * 86400 + 6 * 3600
