"""Dial-in hints after a disappointing shot.

The suggestions follow the dial-in guide from modsmthng's Automatic Pro cheat
sheet (https://modsmthng.github.io/Automatic-Pro/v3/): adjust grind first,
then ratio, then temperature — one variable at a time; sour points to
under-extraction, bitter to over-extraction. Full credit to modsmthng.
"""

from __future__ import annotations

CREDIT = "Guide: modsmthng's Automatic Pro cheat sheet, modsmthng.github.io/Automatic-Pro"


def _ratio(notes: dict) -> float | None:
    try:
        value = float(notes.get("ratio", ""))
        return value if value > 0 else None
    except (TypeError, ValueError):
        return None


def make_hint(notes: dict) -> str | None:
    """A short suggestion for the next shot, or None when the shot was fine."""
    taste = notes.get("balanceTaste")
    rating = notes.get("rating") or 0
    if taste not in ("sour", "bitter") and rating > 2:
        return None
    if taste not in ("sour", "bitter") and not rating:
        return None

    grind = notes.get("grindSetting")
    ratio = _ratio(notes)
    at_grind = f" (you're at {grind})" if grind else ""

    if taste == "sour":
        steps = [f"try a slightly finer grind{at_grind}"]
        if ratio is not None and ratio < 2.0:
            steps.append(f"or lengthen the ratio toward 1:2 (currently 1:{ratio:.2f})")
        steps.append("or +1 °C if the grind is already dialed")
        head = "Sour usually means under-extraction"
    elif taste == "bitter":
        steps = [f"try a slightly coarser grind{at_grind}"]
        if ratio is not None and ratio > 2.0:
            steps.append(f"or shorten the ratio toward 1:2 (currently 1:{ratio:.2f})")
        steps.append("or −1 °C if the grind is already dialed")
        head = "Bitter usually means over-extraction"
    else:  # low rating, no taste direction
        return (
            f"💡 For the next one: adjust the grind first{at_grind}, then ratio, "
            f"then temperature — one variable at a time.\n({CREDIT})"
        )

    return f"💡 {head} — {steps[0]}, {'; '.join(steps[1:])}. One variable at a time.\n({CREDIT})"
