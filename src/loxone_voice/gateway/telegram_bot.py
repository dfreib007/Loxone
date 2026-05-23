"""Telegram channel wiring for the gateway.

A thin shell over :class:`loxone_voice.gateway.core.Gateway`: it
translates aiogram ``Message`` and ``CallbackQuery`` events into
``handle_text`` / ``confirm_action`` calls and sends the response back.
All the policy (whitelist, rate limit, audit, confirmation gate) lives
in :class:`Gateway`, not here.

The handlers are *methods* on :class:`TelegramGateway` rather than
closures inside a factory function. That lets unit tests call them
directly with hand-rolled fakes, without spinning up a dispatcher or
hitting the network.
"""

from __future__ import annotations

import logging
from typing import Any

from aiogram import Bot, Dispatcher, F
from aiogram.filters import Command
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from .core import Gateway

logger = logging.getLogger(__name__)

# Callback-data strings. Kept short because Telegram caps the field at
# 64 bytes. We don't carry action ids in the payload — when the user
# clicks, we ask the gateway for the current pending set instead.
_CALLBACK_PREFIX = "cnf"
_CALLBACK_YES = f"{_CALLBACK_PREFIX}:y"
_CALLBACK_NO = f"{_CALLBACK_PREFIX}:n"

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
_NO_PENDING_TEXT = "Keine offene Aktion mehr (eventuell schon bestätigt oder abgelaufen)."


def _confirmation_keyboard() -> InlineKeyboardMarkup:
    """Yes/No buttons attached to the message that's awaiting confirmation."""
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="✅ Ja, ausführen", callback_data=_CALLBACK_YES),
                InlineKeyboardButton(text="❌ Abbrechen", callback_data=_CALLBACK_NO),
            ]
        ]
    )


class TelegramGateway:
    """Adapter from aiogram events to :class:`Gateway` calls."""

    def __init__(self, gateway: Gateway) -> None:
        self._gateway = gateway

    # ---- Slash commands ----------------------------------------------------

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

    # ---- Plain text + voice -----------------------------------------------

    async def on_text(self, message: Any) -> None:
        user_id = self._user_id(message)
        text = getattr(message, "text", None)
        if user_id is None or not isinstance(text, str):
            return
        response = await self._gateway.handle_text(user_id=user_id, text=text)
        reply_markup = _confirmation_keyboard() if response.pending_actions else None
        await self._answer(message, response.text, reply_markup=reply_markup)

    async def on_voice(self, message: Any) -> None:
        await self._answer(message, _VOICE_NOT_YET_SUPPORTED)

    # ---- Inline confirmation button ---------------------------------------

    async def on_confirmation_callback(self, callback_query: Any) -> None:
        user_id = self._user_id(callback_query)
        data = getattr(callback_query, "data", None)
        if user_id is None or data not in (_CALLBACK_YES, _CALLBACK_NO):
            await self._ack_callback(callback_query, "")
            return

        approved = data == _CALLBACK_YES
        action_ids = self._gateway.pending_action_ids(user_id)
        if not action_ids:
            await self._ack_callback(callback_query, _NO_PENDING_TEXT)
            await self._send_via_callback(callback_query, _NO_PENDING_TEXT)
            return

        response = await self._gateway.confirm_action(
            user_id=user_id, action_ids=action_ids, approved=approved
        )
        await self._ack_callback(callback_query, "")
        await self._send_via_callback(callback_query, response.text)

    # ---- helpers -----------------------------------------------------------

    @staticmethod
    def _user_id(event: Any) -> int | None:
        user = getattr(event, "from_user", None)
        if user is None:
            return None
        user_id = getattr(user, "id", None)
        return user_id if isinstance(user_id, int) else None

    @staticmethod
    async def _answer(
        message: Any, text: str, *, reply_markup: InlineKeyboardMarkup | None = None
    ) -> None:
        answer = getattr(message, "answer", None)
        if answer is None:
            logger.warning("message object has no .answer method; dropping reply")
            return
        if reply_markup is not None:
            await answer(text, reply_markup=reply_markup)
        else:
            await answer(text)

    @staticmethod
    async def _ack_callback(callback_query: Any, text: str) -> None:
        """Tell Telegram to clear the button's spinner."""
        answer = getattr(callback_query, "answer", None)
        if answer is None:
            return
        await answer(text)

    @classmethod
    async def _send_via_callback(cls, callback_query: Any, text: str) -> None:
        """Reply to a callback by sending a new message in the same chat."""
        message = getattr(callback_query, "message", None)
        if message is None:
            return
        await cls._answer(message, text)


def build_dispatcher(telegram_gateway: TelegramGateway) -> Dispatcher:
    """Wire up the Telegram-side handlers.

    Slash commands are registered first so they take precedence over the
    plain-text catch-all. The callback-query handler is filtered by the
    ``cnf:`` prefix so unrelated inline-button events don't reach us.
    """
    dp = Dispatcher()
    dp.message.register(telegram_gateway.on_start, Command("start"))
    dp.message.register(telegram_gateway.on_help, Command("help"))
    dp.message.register(telegram_gateway.on_reset, Command("reset"))
    dp.message.register(telegram_gateway.on_voice, F.voice)
    dp.message.register(telegram_gateway.on_text, F.text)
    dp.callback_query.register(
        telegram_gateway.on_confirmation_callback,
        F.data.in_({_CALLBACK_YES, _CALLBACK_NO}),
    )
    return dp


async def run_polling(bot: Bot, dispatcher: Dispatcher) -> None:
    """Start long-polling. Blocks until the bot is stopped."""
    logger.info("starting Telegram long-polling")
    try:
        await dispatcher.start_polling(bot)
    finally:
        await bot.session.close()
