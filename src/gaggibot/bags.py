"""Bean bag tracking — strictly opt-in.

Nothing happens until the user registers a bag with ``/newbag``; from then on
every logged dose-in is subtracted, and the bot warns once when roughly three
doses are left. ``/bag`` shows the current state at any time.

State shape (in the bot's state file):
    bag = {"name": str, "total_g": float, "used_g": float,
           "shots": int, "rating_sum": int, "warned": bool}
"""

from __future__ import annotations

DEFAULT_DOSE_G = 18.0
WARN_DOSES = 3


def open_bag(state, total_g: float, name: str) -> str:
    state.set("bag", {
        "name": name, "total_g": total_g, "used_g": 0.0,
        "shots": 0, "rating_sum": 0, "warned": False,
    })
    return f"🫘 New bag registered: {name}, {total_g:.0f} g. I'll keep count."


def bag_status(state) -> str:
    bag = state.get("bag")
    if not bag:
        return "No bag registered. Start one with: /newbag <grams> [name]"
    remaining = max(0.0, bag["total_g"] - bag["used_g"])
    dose = _typical_dose(state)
    doses_left = int(remaining // dose) if dose else 0
    line = (
        f"🫘 {bag['name']}: {remaining:.0f} g of {bag['total_g']:.0f} g left"
        f" (≈{doses_left} doses) · {bag['shots']} shots"
    )
    if bag["shots"] and bag["rating_sum"]:
        line += f" · avg {'★' * round(bag['rating_sum'] / bag['shots'])}"
    return line


def track_shot(state, notes: dict) -> str | None:
    """Subtract the logged dose; returns a warning message when running low."""
    bag = state.get("bag")
    if not bag:
        return None
    try:
        dose = float(notes.get("doseIn", ""))
    except (TypeError, ValueError):
        dose = 0.0
    if dose <= 0:
        return None
    bag["used_g"] += dose
    bag["shots"] += 1
    rating = notes.get("rating")
    if isinstance(rating, int):
        bag["rating_sum"] += rating
    remaining = bag["total_g"] - bag["used_g"]

    message = None
    if remaining <= 0:
        message = f"🫘 That was the last of '{bag['name']}' by my count — bag empty."
    elif remaining < WARN_DOSES * dose and not bag["warned"]:
        bag["warned"] = True
        message = (
            f"🫘 Heads-up: '{bag['name']}' is down to ~{remaining:.0f} g "
            f"(≈{int(remaining // dose)} doses). Time to restock?"
        )
    state.set("bag", bag)
    return message


def _typical_dose(state) -> float:
    try:
        return float(state.get("last_notes", {}).get("doseIn", DEFAULT_DOSE_G))
    except (TypeError, ValueError):
        return DEFAULT_DOSE_G
