"""Discord backend (discord.py v2: gateway websocket + button Views)."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator

from .base import Event, Messenger, Option, OptionSelected, TextReply

log = logging.getLogger(__name__)


class DiscordMessenger(Messenger):
    def __init__(self, token: str, channel_id: int | str) -> None:
        import discord

        self.token = token
        self.channel_id = int(channel_id)
        self._queue: asyncio.Queue[Event] = asyncio.Queue()
        self._ready = asyncio.Event()
        self._task: asyncio.Task | None = None

        intents = discord.Intents.default()
        intents.message_content = True
        self.client = discord.Client(intents=intents)

        @self.client.event
        async def on_ready():
            self._ready.set()
            log.info("discord messenger ready in channel %s", self.channel_id)

        @self.client.event
        async def on_message(message):
            if message.author == self.client.user or message.channel.id != self.channel_id:
                return
            await self._queue.put(TextReply(message.content))

        @self.client.event
        async def on_interaction(interaction):
            if interaction.type == discord.InteractionType.component:
                await interaction.response.defer()
                await self._queue.put(OptionSelected(interaction.data["custom_id"]))

    async def start(self) -> None:
        self._task = asyncio.create_task(self.client.start(self.token))
        await asyncio.wait_for(self._ready.wait(), timeout=60)

    async def stop(self) -> None:
        await self.client.close()
        if self._task:
            self._task.cancel()

    def _view(self, options: list[Option] | None):
        if not options:
            return None
        import discord

        view = discord.ui.View(timeout=None)
        for o in options:
            view.add_item(discord.ui.Button(label=o.label, custom_id=o.id))
        return view

    async def send(self, text: str, options: list[Option] | None = None) -> str:
        channel = self.client.get_channel(self.channel_id)
        msg = await channel.send(text, view=self._view(options))
        return str(msg.id)

    async def edit(self, ref: str, text: str, options: list[Option] | None = None) -> None:
        try:
            channel = self.client.get_channel(self.channel_id)
            msg = await channel.fetch_message(int(ref))
            await msg.edit(content=text, view=self._view(options))
        except Exception as exc:  # noqa: BLE001 - edits are cosmetic
            log.debug("edit failed: %s", exc)

    async def events(self) -> AsyncIterator[Event]:
        while True:
            yield await self._queue.get()
