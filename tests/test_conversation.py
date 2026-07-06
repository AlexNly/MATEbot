import pytest

from matebot.conversation import Conversation
from matebot.messengers.base import Messenger, Option, OptionSelected, TextReply
from matebot.state import State


class FakeMessenger(Messenger):
    def __init__(self):
        self.sent: list[tuple[str, list[Option] | None]] = []

    async def start(self): ...
    async def stop(self): ...

    async def send(self, text, options=None):
        self.sent.append((text, options))
        return str(len(self.sent))

    async def edit(self, ref, text, options=None): ...

    def events(self):
        raise NotImplementedError

    @property
    def last_options(self):
        return self.sent[-1][1]


class SaveRecorder:
    def __init__(self, ok=True):
        self.calls = []
        self.ok = ok

    async def __call__(self, shot_id, notes):
        self.calls.append((shot_id, notes))
        return self.ok


@pytest.fixture
def convo(tmp_path):
    fm = FakeMessenger()
    save = SaveRecorder()
    c = Conversation(fm, State(tmp_path / "state.json"), save)
    return c, fm, save


async def answer(c, option_id):
    await c.handle_event(OptionSelected(option_id))


@pytest.mark.asyncio
async def test_full_flow_saves_notes(convo):
    c, fm, save = convo
    await c.start_shot(59, "Direct Lever v3", 32000, 36.4)
    assert "Shot #59" in fm.sent[0][0]
    assert [o.label for o in fm.last_options] == ["★", "★★", "★★★", "★★★★", "★★★★★"]

    await answer(c, "g|59|r|4")
    await answer(c, "g|59|bt|balanced")
    await c.handle_event(TextReply("Mondo Classico"))
    await c.handle_event(TextReply("1 - 0.2"))
    await c.handle_event(TextReply("18"))
    # dose_out has a "Use 36.4 g" prefill option
    assert any("36.4" in o.label for o in fm.last_options)
    await answer(c, "g|59|dout|36.4")
    await c.handle_event(TextReply("Very good, going finer helped"))

    assert save.calls == [
        (
            59,
            {
                "rating": 4,
                "balanceTaste": "balanced",
                "beanType": "Mondo Classico",
                "grindSetting": "1 - 0.2",
                "doseIn": "18",
                "doseOut": "36.4",
                "notes": "Very good, going finer helped",
                "ratio": "2.02",
            },
        )
    ]
    assert "✅" in fm.sent[-1][0]
    assert c.pending is None


@pytest.mark.asyncio
async def test_same_as_last_defaults(tmp_path):
    fm = FakeMessenger()
    save = SaveRecorder()
    state = State(tmp_path / "state.json")
    state.set("last_notes", {"beanType": "Mondo Classico", "grindSetting": "1", "doseIn": "18"})
    c = Conversation(fm, state, save)
    await c.start_shot(60, "Classic v3", 30000, 0)
    await answer(c, "g|60|r|5")
    await answer(c, "g|60|bt|skip")
    # bean prompt should offer the previous bean
    same = [o for o in fm.last_options if "Same as last" in o.label]
    assert same and "Mondo Classico" in same[0].label
    await answer(c, same[0].id)
    assert save.calls == []  # not finished yet
    await answer(c, "g|60|grind|skip")
    await answer(c, "g|60|din|skip")
    await answer(c, "g|60|dout|skip")
    await answer(c, "g|60|txt|skip")
    (sid, notes), = save.calls
    assert sid == 60
    assert notes == {"rating": 5, "beanType": "Mondo Classico"}


@pytest.mark.asyncio
async def test_stale_and_malformed_options_ignored(convo):
    c, fm, save = convo
    await c.start_shot(61, "p", 30000, 0)
    before = len(fm.sent)
    await answer(c, "g|60|r|4")  # wrong shot
    await answer(c, "g|61|bt|balanced")  # wrong step (still on rating)
    await answer(c, "garbage")
    assert len(fm.sent) == before  # no advancement

    await answer(c, "g|61|r|3")
    assert len(fm.sent) == before + 1


@pytest.mark.asyncio
async def test_supersede_saves_partial_notes(convo):
    c, fm, save = convo
    await c.start_shot(62, "p", 30000, 0)
    await answer(c, "g|62|r|2")
    await c.start_shot(63, "p", 31000, 0)  # new shot arrives mid-questionnaire
    assert save.calls[0][0] == 62
    assert save.calls[0][1]["rating"] == 2
    assert c.pending.shot_id == 63


@pytest.mark.asyncio
async def test_pending_survives_restart(tmp_path):
    fm = FakeMessenger()
    save = SaveRecorder()
    state_path = tmp_path / "state.json"
    c = Conversation(fm, State(state_path), save)
    await c.start_shot(64, "p", 30000, 0)
    await answer(c, "g|64|r|4")

    c2 = Conversation(FakeMessenger(), State(state_path), save)
    assert c2.pending.shot_id == 64
    assert c2.pending.step == "bt"
    assert c2.pending.answers == {"rating": "4"}


@pytest.mark.asyncio
async def test_save_failure_reported(tmp_path):
    fm = FakeMessenger()
    save = SaveRecorder(ok=False)
    c = Conversation(fm, State(tmp_path / "state.json"), save)
    await c.start_shot(65, "p", 30000, 0)
    await answer(c, "g|65|r|1")
    for step in ("bt", "bean", "grind", "din", "dout", "txt"):
        await answer(c, f"g|65|{step}|skip")
    assert "⚠️" in fm.sent[-1][0]


def test_signoff_matches_time_of_day():
    from matebot.conversation import _signoff

    assert "kickstart" in _signoff(7)      # 7 am is not bedtime
    assert "Back to it" in _signoff(13)
    assert "evening" in _signoff(19)
    assert "dreams" in _signoff(23)
    assert "dreams" in _signoff(2)
