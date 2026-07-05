"""Telegram backend (python-telegram-bot v20+ async API, long-polling)."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator

from .base import Event, Messenger, Option, OptionSelected, TextReply

log = logging.getLogger(__name__)


class TelegramMessenger(Messenger):
    def __init__(self, token: str, chat_id: int | str) -> None:
        from telegram.ext import (
            Application,
            CallbackQueryHandler,
            MessageHandler,
            filters,
        )

        self.chat_id = int(chat_id)
        self._queue: asyncio.Queue[Event] = asyncio.Queue()
        self.app = Application.builder().token(token).build()
        self.app.add_handler(CallbackQueryHandler(self._on_callback))
        self.app.add_handler(
            MessageHandler(filters.TEXT & filters.Chat(self.chat_id), self._on_text)
        )

    async def _on_callback(self, update, context) -> None:
        query = update.callback_query
        await query.answer()
        if query.message and query.message.chat_id != self.chat_id:
            return
        await self._queue.put(OptionSelected(query.data))

    async def _on_text(self, update, context) -> None:
        await self._queue.put(TextReply(update.message.text))

    async def start(self) -> None:
        await self.app.initialize()
        await self.app.start()
        await self.app.updater.start_polling(drop_pending_updates=True)
        log.info("telegram messenger polling as chat %s", self.chat_id)

    async def stop(self) -> None:
        await self.app.updater.stop()
        await self.app.stop()
        await self.app.shutdown()

    def _keyboard(self, options: list[Option] | None):
        if not options:
            return None
        from telegram import InlineKeyboardButton, InlineKeyboardMarkup

        # ratings in one row, everything else stacked
        if all(o.id.split("|")[2] == "r" for o in options):
            rows = [[InlineKeyboardButton(o.label, callback_data=o.id) for o in options]]
        else:
            rows = [[InlineKeyboardButton(o.label, callback_data=o.id)] for o in options]
        return InlineKeyboardMarkup(rows)

    async def send(self, text: str, options: list[Option] | None = None) -> str:
        msg = await self.app.bot.send_message(
            self.chat_id, text, reply_markup=self._keyboard(options)
        )
        return str(msg.message_id)

    async def edit(self, ref: str, text: str, options: list[Option] | None = None) -> None:
        try:
            await self.app.bot.edit_message_text(
                text, chat_id=self.chat_id, message_id=int(ref),
                reply_markup=self._keyboard(options),
            )
        except Exception as exc:  # noqa: BLE001 - edits are cosmetic
            log.debug("edit failed: %s", exc)

    async def events(self) -> AsyncIterator[Event]:
        while True:
            yield await self._queue.get()
