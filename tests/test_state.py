"""State resilience: an unwritable state dir must not break the bot."""

import logging

from matebot.state import State


def test_state_roundtrip(tmp_path):
    s = State(tmp_path / "state.json")
    s.set("bags", [{"name": "x"}])
    assert s.persistent
    s2 = State(tmp_path / "state.json")
    assert s2.get("bags") == [{"name": "x"}]


def test_unwritable_dir_degrades_to_memory(tmp_path, monkeypatch, caplog):
    s = State(tmp_path / "state.json")

    def boom(*a, **k):
        raise PermissionError(13, "Permission denied")

    monkeypatch.setattr("matebot.state.tempfile.mkstemp", boom)
    assert not s.persistent
    with caplog.at_level(logging.ERROR):
        s.set("bags", [{"name": "x"}])  # must not raise
        s.set("bags", [{"name": "y"}])  # warning only once
    assert s.get("bags") == [{"name": "y"}]  # in-memory state still works
    assert sum("not writable" in r.message for r in caplog.records) == 1
