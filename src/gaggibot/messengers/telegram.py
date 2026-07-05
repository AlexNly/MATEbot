"""Telegram backend (python-telegram-bot v20+ async API, long-polling)."""

from __future__ import annotations

import asyncio
import contextlib
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
        from telegram import BotCommand

        await self.app.initialize()
        await self.app.start()
        with contextlib.suppress(Exception):  # menu is cosmetic
            await self.app.bot.set_my_commands([
                BotCommand("wake", "turn the machine on, ping when hot"),
                BotCommand("sleep", "machine to standby"),
                BotCommand("status", "mode, temperature, connectivity"),
                BotCommand("last", "last logged shot"),
                BotCommand("fix", "redo the last shot's log"),
                BotCommand("bag", "how much is left in the bean bag"),
                BotCommand("newbag", "start tracking a bean bag"),
                BotCommand("help", "list commands"),
            ])
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

        buttons = [InlineKeyboardButton(o.label, callback_data=o.id) for o in options]
        # ratings in rows of three (a single row of five truncates on phones),
        # everything else stacked
        if all(o.id.split("|")[2] == "r" for o in options):
            rows = [buttons[i : i + 3] for i in range(0, len(buttons), 3)]
        else:
            rows = [[b] for b in buttons]
        return InlineKeyboardMarkup(rows)

    async def send(self, text: str, options: list[Option] | None = None) -> str:
        # telegram API connectivity can flap; a lost send must not crash the bot
        last_exc = None
        for attempt in range(5):
            try:
                msg = await self.app.bot.send_message(
                    self.chat_id, text, reply_markup=self._keyboard(options)
                )
                return str(msg.message_id)
            except Exception as exc:  # noqa: BLE001 - NetworkError et al.
                last_exc = exc
                log.warning("send attempt %d failed: %s", attempt + 1, exc)
                await asyncio.sleep(2 * (attempt + 1))
        raise last_exc

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
