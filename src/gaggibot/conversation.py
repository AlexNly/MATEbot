"""The post-shot questionnaire — messenger-agnostic.

One questionnaire at a time (it's a home espresso machine, not a fleet).
A new shot supersedes a pending questionnaire: whatever was already answered
is saved (partial notes beat no notes), then the new one starts.

Flow: RATING → TASTE → BEAN → GRIND → DOSE_IN → DOSE_OUT → NOTES → save.
Answers land in the machine's own "Shot Notes" (via req:history:notes:save),
exactly like typing them into the web UI — just without opening the web UI.
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from .messengers.base import Event, Messenger, Option, OptionSelected, TextReply
from .state import State

log = logging.getLogger(__name__)

SKIP = "–"  # sentinel label value for skip options

# field key -> notes key
NOTE_KEYS = {
    "r": "rating",
    "bt": "balanceTaste",
    "bean": "beanType",
    "grind": "grindSetting",
    "din": "doseIn",
    "dout": "doseOut",
    "txt": "notes",
}

STEPS = ["r", "bt", "bean", "grind", "din", "dout", "txt"]
TEXT_STEPS = {"bean", "grind", "din", "dout", "txt"}

PROMPTS = {
    "r": "How was it?",
    "bt": "Balance / taste?",
    "bean": "Which beans?",
    "grind": "Grind setting?",
    "din": "Dose in (g)?",
    "dout": "Dose out (g)?",
    "txt": "Any notes for future-you?",
}


def _fmt_duration(ms: int) -> str:
    return f"{ms / 1000:.0f}s"


@dataclass
class PendingShot:
    shot_id: int
    profile: str
    duration_ms: int
    volume_g: float
    step: str = "r"
    answers: dict | None = None

    def to_dict(self) -> dict:
        return {
            "shot_id": self.shot_id,
            "profile": self.profile,
            "duration_ms": self.duration_ms,
            "volume_g": self.volume_g,
            "step": self.step,
            "answers": self.answers or {},
        }

    @classmethod
    def from_dict(cls, d: dict) -> PendingShot:
        return cls(
            shot_id=d["shot_id"],
            profile=d.get("profile", ""),
            duration_ms=d.get("duration_ms", 0),
            volume_g=d.get("volume_g", 0.0),
            step=d.get("step", "r"),
            answers=d.get("answers") or {},
        )


class Conversation:
    def __init__(
        self,
        messenger: Messenger,
        state: State,
        save_notes: Callable[[int, dict], Awaitable[bool]],
    ) -> None:
        self.messenger = messenger
        self.state = state
        self.save_notes = save_notes
        self.pending: PendingShot | None = None
        restored = state.get("pending")
        if restored:
            self.pending = PendingShot.from_dict(restored)
        self._msg_ref: str | None = None

    # ------------------------------------------------------------- shots

    async def start_shot(
        self, shot_id: int, profile: str, duration_ms: int, volume_g: float
    ) -> None:
        if self.pending is not None:
            await self._finish(superseded_by=shot_id)
        self.pending = PendingShot(shot_id, profile, duration_ms, volume_g, answers={})
        self._persist()
        summary = (
            f"☕ Shot #{shot_id} done!\n"
            f"{profile} · {_fmt_duration(duration_ms)}"
            + (f" · {volume_g:.1f} g in the cup" if volume_g else "")
            + "\n\nLet's log it before you forget:"
        )
        await self.messenger.send(summary)
        await self._prompt()

    async def resume_if_pending(self) -> None:
        if self.pending is not None:
            await self.messenger.send(
                f"☕ Shot #{self.pending.shot_id} is still waiting for its log — "
                "where were we?"
            )
            await self._prompt()

    # ------------------------------------------------------------- events

    async def handle_event(self, event: Event) -> None:
        if self.pending is None:
            return
        if isinstance(event, OptionSelected):
            await self._handle_option(event.option_id)
        elif isinstance(event, TextReply):
            await self._handle_text(event.text)

    async def _handle_option(self, option_id: str) -> None:
        try:
            tag, shot, field, value = option_id.split("|", 3)
        except ValueError:
            return
        if tag != "g" or self.pending is None:
            return
        if int(shot) != self.pending.shot_id or field != self.pending.step:
            log.debug("stale option %s ignored", option_id)
            return
        if value == "skip":
            await self._advance(None)
        else:
            await self._advance(value)

    async def _handle_text(self, text: str) -> None:
        if self.pending is None or self.pending.step not in TEXT_STEPS:
            return
        await self._advance(text.strip())

    # ------------------------------------------------------------- steps

    def _defaults(self) -> dict:
        return self.state.get("last_notes", {})

    def _options_for(self, step: str) -> list[Option]:
        sid = self.pending.shot_id
        mk = lambda field, value, label: Option(f"g|{sid}|{field}|{value}", label)  # noqa: E731
        if step == "r":
            return [mk("r", str(n), "★" * n) for n in range(1, 6)]
        if step == "bt":
            return [
                mk("bt", "sour", "🍋 sour"),
                mk("bt", "balanced", "⚖️ balanced"),
                mk("bt", "bitter", "🌰 bitter"),
                mk("bt", "skip", "skip"),
            ]
        options = []
        default = self._defaults().get(NOTE_KEYS[step])
        if step == "dout" and self.pending.volume_g:
            grams = f"{self.pending.volume_g:.1f}"
            options.append(mk("dout", grams, f"Use {grams} g"))
        elif default:
            label = f"Same as last: {default}"
            options.append(mk(step, default, label[:60]))
        options.append(mk(step, "skip", "skip"))
        return options

    async def _prompt(self) -> None:
        step = self.pending.step
        text = PROMPTS[step]
        if step in TEXT_STEPS and step != "txt":
            text += " (tap or just type)"
        self._msg_ref = await self.messenger.send(text, self._options_for(step))

    async def _advance(self, value: str | None) -> None:
        step = self.pending.step
        if value:
            self.pending.answers[NOTE_KEYS[step]] = value
        idx = STEPS.index(step)
        if idx + 1 < len(STEPS):
            self.pending.step = STEPS[idx + 1]
            self._persist()
            await self._prompt()
        else:
            await self._finish()

    # ------------------------------------------------------------- finish

    async def _finish(self, superseded_by: int | None = None) -> None:
        pending, self.pending = self.pending, None
        self._persist()
        answers = dict(pending.answers or {})
        if "rating" in answers:
            answers["rating"] = int(answers["rating"])
        din, dout = answers.get("doseIn"), answers.get("doseOut")
        try:
            if din and dout and float(din) > 0:
                answers["ratio"] = f"{float(dout) / float(din):.2f}"
        except ValueError:
            pass

        if superseded_by is not None and not answers:
            await self.messenger.send(
                f"⏭ Shot #{pending.shot_id} skipped (new shot #{superseded_by})."
            )
            return

        ok = await self.save_notes(pending.shot_id, answers) if answers else True
        if ok:
            # remember defaults for the next "same as last" buttons
            last = self.state.get("last_notes", {})
            remembered = ("beanType", "grindSetting", "doseIn")
            last.update({k: v for k, v in answers.items() if k in remembered})
            self.state.set("last_notes", last)
            stars = "★" * int(answers.get("rating", 0))
            suffix = f" (superseded by #{superseded_by})" if superseded_by else ""
            ratio = f" · 1:{answers['ratio']}" if answers.get("ratio") else ""
            await self.messenger.send(
                f"✅ Shot #{pending.shot_id} logged{suffix}. {stars}{ratio}\n"
                "Filed straight into your GaggiMate shot notes. Sweet dreams tonight."
            )
        else:
            await self.messenger.send(
                f"⚠️ Couldn't reach the machine to save notes for shot #{pending.shot_id}. "
                "They'll appear once it's back online — or re-enter them in the web UI."
            )

    def _persist(self) -> None:
        self.state.set("pending", self.pending.to_dict() if self.pending else None)
