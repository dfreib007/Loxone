"""Telegram channel wiring for the gateway.

A thin shell over :class:`loxone_voice.gateway.core.Gateway`: it
translates aiogram ``Message`` objects into ``gateway.handle_text``
calls and sends the response back. All the rules (whitelist, rate
limit, audit) live in :class:`Gateway`, not here.

The handlers are *methods* on :class:`TelegramGateway` rather than
closures inside a factory function. That lets unit tests call them
directly with a fake :class:`aiogram.types.Message`, without spinning
up a dispatcher or hitting the network.
"""

from __future__ import annotations

import logging
from typing import Any

from aiogram import Bot, Dispatcher, F
from aiogram.filters import Command

from .core import Gateway

logger = logging.getLogger(__name__)

_HELP_TEXT = (
    "Schreib mir einen Befehl in natürlicher Sprache, z. B. "
    '"Mach das Wohnzimmerlicht aus" oder "Sind alle Fenster zu?". '
    "\n\n"
    "Verfügbare Slash-Befehle:\n"
    "  /start - Begrüßung\n"
    "  /help  - diese Hilfe\n"
    "  /reset - Konversationskontext zurücksetzen\n"
)

_START_TEXT = (
    "Hallo. Ich kann dein Loxone Smart Home für dich steuern.\n"
    "Schreib mir, was du machen willst — auf Deutsch oder Englisch."
)

_VOICE_NOT_YET_SUPPORTED = "Sprachnachrichten unterstütze ich noch nicht — bitte als Text schicken."


class TelegramGateway:
    """Adapter from aiogram ``Message`` events to :class:`Gateway` calls."""

    def __init__(self, gateway: Gateway) -> None:
        self._gateway = gateway

    async def on_start(self, message: Any) -> None:
        await self._answer(message, _START_TEXT)

    async def on_help(self, message: Any) -> None:
        await self._answer(message, _HELP_TEXT)

    async def on_reset(self, message: Any) -> None:
        user_id = self._user_id(message)
        if user_id is None:
            return
        self._gateway.reset_user(user_id)
        await self._answer(message, "Konversation zurückgesetzt.")

    async def on_text(self, message: Any) -> None:
        user_id = self._user_id(message)
        text = getattr(message, "text", None)
        if user_id is None or not isinstance(text, str):
            return
        response = await self._gateway.handle_text(user_id=user_id, text=text)
        await self._answer(message, response.text)

    async def on_voice(self, message: Any) -> None:
        await self._answer(message, _VOICE_NOT_YET_SUPPORTED)

    # ---- helpers -----------------------------------------------------------

    @staticmethod
    def _user_id(message: Any) -> int | None:
        user = getattr(message, "from_user", None)
        if user is None:
            return None
        user_id = getattr(user, "id", None)
        return user_id if isinstance(user_id, int) else None

    @staticmethod
    async def _answer(message: Any, text: str) -> None:
        answer = getattr(message, "answer", None)
        if answer is None:
            logger.warning("message object has no .answer method; dropping reply")
            return
        await answer(text)


def build_dispatcher(telegram_gateway: TelegramGateway) -> Dispatcher:
    """Wire up the Telegram-side handlers.

    The slash-command handlers are registered first so they take
    precedence over the plain-text catch-all.
    """
    dp = Dispatcher()
    dp.message.register(telegram_gateway.on_start, Command("start"))
    dp.message.register(telegram_gateway.on_help, Command("help"))
    dp.message.register(telegram_gateway.on_reset, Command("reset"))
    dp.message.register(telegram_gateway.on_voice, F.voice)
    dp.message.register(telegram_gateway.on_text, F.text)
    return dp


async def run_polling(bot: Bot, dispatcher: Dispatcher) -> None:
    """Start long-polling. Blocks until the bot is stopped."""
    logger.info("starting Telegram long-polling")
    try:
        await dispatcher.start_polling(bot)
    finally:
        await bot.session.close()
