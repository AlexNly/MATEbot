"""Bean bag tracking — strictly opt-in, multiple open bags supported.

Nothing happens until the user registers a bag with ``/newbag``. Open bags
appear as tap options in the questionnaire's bean question; a logged dose is
subtracted from the bag whose name matches the answered bean. ``/bag`` shows
all open bags, ``/tossbag`` closes one (emptied, binned, or gifted).

State shape: ``bags = [{"name", "total_g", "used_g", "shots", "rating_sum",
"warned"}, ...]`` (the legacy single-``bag`` key is migrated on first access).
"""

from __future__ import annotations

DEFAULT_DOSE_G = 18.0
WARN_DOSES = 3
MAX_NAME_LEN = 40  # keeps bean names inside messenger option-id limits


def _bags(state) -> list[dict]:
    bags = state.get("bags")
    if bags is None:
        legacy = state.get("bag")
        bags = [legacy] if legacy else []
        state.set("bags", bags)
        state.set("bag", None)
    return bags


def open_bag_names(state) -> list[str]:
    return [b["name"] for b in _bags(state)]


def open_bag(state, total_g: float, name: str) -> str:
    name = name.strip()[:MAX_NAME_LEN]
    bags = [b for b in _bags(state) if b["name"] != name]  # same name = new bag
    bags.append({
        "name": name, "total_g": total_g, "used_g": 0.0,
        "shots": 0, "rating_sum": 0, "warned": False,
    })
    state.set("bags", bags)
    return f"🫘 New bag registered: {name}, {total_g:.0f} g. I'll keep count."


def toss_bag(state, name: str | None = None) -> str:
    bags = _bags(state)
    if not bags:
        return "No open bags to toss."
    if name:
        matches = [b for b in bags if b["name"].lower() == name.strip().lower()]
        if not matches:
            return f"No open bag called “{name}”. Open bags: {', '.join(open_bag_names(state))}"
        bag = matches[0]
    elif len(bags) == 1:
        bag = bags[0]
    else:
        return "Which one? /tossbag <name> — open bags: " + ", ".join(open_bag_names(state))
    bags.remove(bag)
    state.set("bags", bags)
    left = max(0.0, bag["total_g"] - bag["used_g"])
    line = f"🗑 Tossed “{bag['name']}” after {bag['shots']} shots"
    if left > 20:
        line += f" (~{left:.0f} g unused — a friend's gain, I hope)"
    return line + "."


def bag_status(state) -> str:
    bags = _bags(state)
    if not bags:
        return "No bag registered. Start one with: /newbag <grams> [name]"
    dose = _typical_dose(state)
    lines = []
    for bag in bags:
        remaining = max(0.0, bag["total_g"] - bag["used_g"])
        doses_left = int(remaining // dose) if dose else 0
        line = (
            f"🫘 {bag['name']}: {remaining:.0f} g of {bag['total_g']:.0f} g left"
            f" (≈{doses_left} doses) · {bag['shots']} shots"
        )
        if bag["shots"] and bag["rating_sum"]:
            line += f" · avg {'★' * round(bag['rating_sum'] / bag['shots'])}"
        lines.append(line)
    return "\n".join(lines)


def track_shot(state, notes: dict) -> str | None:
    """Subtract the logged dose from the matching bag; warn when running low.

    The dose goes to the bag whose name equals the answered bean. With exactly
    one open bag and no bean given, that bag gets it (the pre-multi-bag
    behavior). Ambiguous cases are left untracked rather than guessed.
    """
    bags = _bags(state)
    if not bags:
        return None
    try:
        dose = float(notes.get("doseIn", ""))
    except (TypeError, ValueError):
        dose = 0.0
    if dose <= 0:
        return None

    bean = (notes.get("beanType") or "").strip().lower()
    matches = [b for b in bags if b["name"].lower() == bean]
    if not matches and not bean and len(bags) == 1:
        matches = bags
    if not matches:
        return None
    bag = matches[0]

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
    state.set("bags", bags)
    return message


def _typical_dose(state) -> float:
    try:
        return float(state.get("last_notes", {}).get("doseIn", DEFAULT_DOSE_G))
    except (TypeError, ValueError):
        return DEFAULT_DOSE_G
