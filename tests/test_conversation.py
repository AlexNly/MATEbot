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
async def test_second_shot_queues_and_follows(convo):
    c, fm, save = convo
    await c.start_shot(62, "p", 30000, 0)
    await answer(c, "g|62|r|2")
    await c.start_shot(63, "q", 31000, 0)  # new shot arrives mid-questionnaire
    # first shot is still the one being logged; second is announced and queued
    assert c.pending.shot_id == 62 and c.pending.step == "bt"
    assert [q.shot_id for q in c.queue] == [63]
    assert "Shot #63 done" in fm.sent[-1][0] and "after #62" in fm.sent[-1][0]
    assert save.calls == []

    for step in ("bt", "bean", "grind", "din", "dout", "txt"):
        await answer(c, f"g|62|{step}|skip")
    assert save.calls == [(62, {"rating": 2})]
    # ...and #63 starts automatically
    assert c.pending.shot_id == 63 and c.queue == []
    assert any("Next up: shot #63" in t for t, _ in fm.sent)
    assert [o.id for o in fm.last_options] == [f"g|63|r|{n}" for n in range(1, 6)]


@pytest.mark.asyncio
async def test_skip_moves_to_next(convo):
    c, fm, save = convo
    await c.start_shot(70, "p", 30000, 0)
    await c.start_shot(71, "p", 30000, 0)
    await c.start_shot(72, "p", 30000, 0)
    assert "+1 more" in fm.sent[-1][0]
    assert await c.skip()  # nothing answered -> nothing saved
    assert save.calls == [] and "skipped" in fm.sent[-3][0]
    assert c.pending.shot_id == 71 and [q.shot_id for q in c.queue] == [72]
    await answer(c, "g|71|r|4")
    assert await c.skip()  # partial answers are still saved
    assert save.calls == [(71, {"rating": 4})]
    assert c.pending.shot_id == 72
    for step in ("r", "bt", "bean", "grind", "din", "dout", "txt"):
        await answer(c, f"g|72|{step}|skip")
    assert c.pending is None and not await c.skip()


@pytest.mark.asyncio
async def test_fix_jumps_queue_and_parks_current(convo):
    c, fm, save = convo
    await c.start_shot(80, "p", 30000, 0)
    await answer(c, "g|80|r|3")
    await c.start_shot(81, "p", 30000, 0)
    await c.start_shot(79, "old", 25000, 0, now=True)  # /fix 79
    assert c.pending.shot_id == 79
    assert [q.shot_id for q in c.queue] == [80, 81]
    assert "#80 is parked" in fm.sent[-2][0]
    for step in ("r", "bt", "bean", "grind", "din", "dout", "txt"):
        await answer(c, f"g|79|{step}|skip")
    # back to #80 with its rating intact, on the step it was parked at
    assert c.pending.shot_id == 80 and c.pending.step == "bt"
    assert c.pending.answers == {"rating": "3"}
    assert any("Picking up where we left off" in t for t, _ in fm.sent)
    # /fix on the shot in progress restarts it instead of queueing a duplicate
    await c.start_shot(80, "p", 30000, 0, now=True)
    assert c.pending.shot_id == 80 and c.pending.step == "r" and c.pending.answers == {}
    assert [q.shot_id for q in c.queue] == [81]


@pytest.mark.asyncio
async def test_queue_survives_restart(tmp_path):
    fm = FakeMessenger()
    save = SaveRecorder()
    state_path = tmp_path / "state.json"
    c = Conversation(fm, State(state_path), save)
    await c.start_shot(90, "p", 30000, 0)
    await c.start_shot(91, "p", 30000, 0)

    fm2 = FakeMessenger()
    c2 = Conversation(fm2, State(state_path), save)
    assert c2.pending.shot_id == 90 and [q.shot_id for q in c2.queue] == [91]
    await c2.resume_if_pending()
    assert "1 more shot queued" in fm2.sent[0][0]


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


@pytest.mark.asyncio
async def test_bean_step_offers_open_bags(tmp_path):
    from matebot.bags import open_bag

    fm = FakeMessenger()
    state = State(tmp_path / "state.json")
    open_bag(state, 250, "Mondo Classico")
    open_bag(state, 250, "Ethiopia Natural")
    state.set("last_notes", {"beanType": "Mondo Classico"})
    c = Conversation(fm, state, SaveRecorder())
    await c.start_shot(70, "p", 30000, 0)
    await answer(c, "g|70|r|4")
    await answer(c, "g|70|bt|skip")
    labels = [o.label for o in fm.last_options]
    assert "🫘 Mondo Classico" in labels
    assert "🫘 Ethiopia Natural" in labels
    # no duplicate "Same as last: Mondo Classico" next to the bag button
    assert not any("Same as last" in label for label in labels)
    bag_option = next(o for o in fm.last_options if o.label == "🫘 Ethiopia Natural")
    await c.handle_event(OptionSelected(bag_option.id))
    assert c.pending.answers["beanType"] == "Ethiopia Natural"
