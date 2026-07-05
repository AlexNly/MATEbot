from .base import Event, Messenger, Option, OptionSelected, TextReply


def create_messenger(config) -> Messenger:
    """Instantiate the configured backend (imports lazily: extras are optional)."""
    kind = config.messenger
    if kind == "telegram":
        from .telegram import TelegramMessenger

        return TelegramMessenger(config.telegram_token, config.telegram_chat_id)
    if kind == "discord":
        from .discord import DiscordMessenger

        return DiscordMessenger(config.discord_token, config.discord_channel_id)
    raise ValueError(
        f"unknown messenger {kind!r} (supported: telegram, discord; "
        "matrix is planned — see README for WhatsApp options)"
    )


__all__ = [
    "Event",
    "Messenger",
    "Option",
    "OptionSelected",
    "TextReply",
    "create_messenger",
]
