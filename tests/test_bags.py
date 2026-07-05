from gaggibot.bags import bag_status, open_bag, track_shot
from gaggibot.state import State


def make_state(tmp_path):
    return State(tmp_path / "state.json")


def test_no_bag_is_silent(tmp_path):
    state = make_state(tmp_path)
    assert track_shot(state, {"doseIn": "18", "rating": 4}) is None
    assert "No bag registered" in bag_status(state)


def test_open_and_track(tmp_path):
    state = make_state(tmp_path)
    msg = open_bag(state, 250, "Mondo Classico")
    assert "Mondo Classico" in msg and "250" in msg

    # normal shots: silent
    for _ in range(10):
        assert track_shot(state, {"doseIn": "18", "rating": 4}) is None
    status = bag_status(state)
    assert "70 g of 250 g" in status
    assert "10 shots" in status
    assert "★★★★" in status


def test_warns_once_when_low_then_empty(tmp_path):
    state = make_state(tmp_path)
    open_bag(state, 250, "Test")
    warnings = [track_shot(state, {"doseIn": "18"}) for _ in range(11)]
    low = [w for w in warnings if w and "Heads-up" in w]
    assert len(low) == 1  # warned exactly once (crossing below 3 doses)
    # 13 doses of 18 g = 234 g used; 14th crosses 250
    assert track_shot(state, {"doseIn": "18"}) is None
    assert track_shot(state, {"doseIn": "18"}) is None
    empty = track_shot(state, {"doseIn": "18"})
    assert empty and "empty" in empty


def test_skipped_dose_not_counted(tmp_path):
    state = make_state(tmp_path)
    open_bag(state, 250, "Test")
    assert track_shot(state, {}) is None
    assert track_shot(state, {"doseIn": "garbage"}) is None
    assert "250 g of 250 g" in bag_status(state)
