"""Messenger abstraction: everything the conversation engine needs, nothing more.

A backend renders prompts (text + option buttons where the platform has them),
and yields user interactions as a single event stream. Option ids stay ≤64
bytes so they fit Telegram ``callback_data`` and Discord ``custom_id``.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import AsyncIterator
from dataclasses import dataclass


@dataclass
class Option:
    id: str  # e.g. "g|59|r|4"
    label: str  # e.g. "★★★★"


@dataclass
class OptionSelected:
    option_id: str


@dataclass
class TextReply:
    text: str


Event = OptionSelected | TextReply


class Messenger(ABC):
    """One chat/channel with one user; the espresso machine's voice."""

    @abstractmethod
    async def start(self) -> None: ...

    @abstractmethod
    async def stop(self) -> None: ...

    @abstractmethod
    async def send(self, text: str, options: list[Option] | None = None) -> str:
        """Send a message, return an opaque reference usable with edit()."""

    @abstractmethod
    async def edit(self, ref: str, text: str, options: list[Option] | None = None) -> None:
        """Replace a previously sent message's text/options (best effort)."""

    @abstractmethod
    def events(self) -> AsyncIterator[Event]:
        """User interactions, in order. Runs until stop()."""
