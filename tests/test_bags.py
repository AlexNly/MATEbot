from matebot.bags import bag_status, open_bag, track_shot
from matebot.state import State


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


def test_multi_bag_attribution(tmp_path):
    state = make_state(tmp_path)
    open_bag(state, 250, "Mondo Classico")
    open_bag(state, 500, "Supermarket Blend")
    track_shot(state, {"doseIn": "18", "beanType": "Mondo Classico", "rating": 5})
    track_shot(state, {"doseIn": "20", "beanType": "supermarket blend"})  # case-insensitive
    track_shot(state, {"doseIn": "18"})  # ambiguous with two bags: untracked
    status = bag_status(state)
    assert "232 g of 250 g" in status
    assert "480 g of 500 g" in status


def test_toss_bag(tmp_path):
    from matebot.bags import open_bag_names, toss_bag

    state = make_state(tmp_path)
    assert "No open bags" in toss_bag(state)
    open_bag(state, 250, "Mondo Classico")
    open_bag(state, 250, "Gift Beans")
    assert "Which one?" in toss_bag(state)  # ambiguous
    assert "Gift Beans" in toss_bag(state, "gift beans")
    assert open_bag_names(state) == ["Mondo Classico"]
    assert "Tossed" in toss_bag(state)  # single bag: no name needed
    assert open_bag_names(state) == []


def test_legacy_single_bag_migrates(tmp_path):
    state = make_state(tmp_path)
    state.set("bag", {"name": "Old Beans", "total_g": 250, "used_g": 50,
                      "shots": 3, "rating_sum": 12, "warned": False})
    assert "Old Beans: 200 g" in bag_status(state)
    assert state.get("bag") is None
