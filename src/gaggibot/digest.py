"""Weekly shot digest — a Sunday-evening summary, also available via /digest."""

from __future__ import annotations

import json
import re
from datetime import datetime, timedelta
from pathlib import Path

from .slog import ShotIndex

UTILITY_RE = re.compile(r"(?i)backflush|descale|flush|clean")


def rows_from_index(index: ShotIndex) -> list[dict]:
    return [
        {
            "id": e.id,
            "ts": e.timestamp,
            "duration_ms": e.duration_ms,
            "volume_g": e.volume_g,
            "rating": e.rating,
            "profile": e.profile_name,
        }
        for e in index.entries
        if e.completed and not e.deleted
    ]


def rows_from_site_index(path: str | Path) -> list[dict]:
    data = json.loads(Path(path).read_text())
    return [
        {
            "id": int(s["id"]),
            "ts": s.get("ts", 0),
            "duration_ms": int(s.get("duration_s", 0) * 1000),
            "volume_g": s.get("final_g", 0),
            "rating": s.get("rating", 0),
            "profile": s.get("profile", ""),
        }
        for s in data.get("shots", [])
    ]


def compute(rows: list[dict], *, now: datetime, journal_url: str = "") -> str | None:
    """Digest text for the 7 days before *now*; None when there were no shots."""
    since = (now - timedelta(days=7)).timestamp()
    week = [
        r for r in rows
        if r["ts"] >= since and not UTILITY_RE.search(r["profile"] or "")
        and r["duration_ms"] >= 10_000
    ]
    if not week:
        return None

    total_out = sum(r["volume_g"] or 0 for r in week)
    rated = [r for r in week if r["rating"]]
    lines = [f"📊 Your week in espresso: {len(week)} shots"]
    if total_out:
        lines[0] += f" · {total_out:.0f} g in the cup"
    if rated:
        avg = sum(r["rating"] for r in rated) / len(rated)
        lines.append(f"Average rating {avg:.1f}★ ({len(rated)} rated)")
        best = max(rated, key=lambda r: (r["rating"], r["id"]))
        best_line = f"Best shot: #{best['id']} ({'★' * best['rating']})"
        if journal_url:
            best_line += f" {journal_url.rstrip('/')}/#{best['id']:06d}"
        lines.append(best_line)
    return "\n".join(lines)


def seconds_until_sunday_evening(now: datetime, hour: int = 18) -> float:
    """Seconds until the next Sunday at *hour* local time (>= 60 s away)."""
    days_ahead = (6 - now.weekday()) % 7
    target = (now + timedelta(days=days_ahead)).replace(
        hour=hour, minute=0, second=0, microsecond=0
    )
    if target <= now:
        target += timedelta(days=7)
    return max(60.0, (target - now).total_seconds())
