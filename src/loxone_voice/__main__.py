"""Production entry point.

Wires every layer together — Loxone discovery, WebSocket client,
Anthropic-backed intent engine, channel-agnostic gateway, and Telegram
bot — using configuration from ``.env`` / environment variables.

Run via ``python -m loxone_voice`` or the Docker entrypoint.
"""

from __future__ import annotations

import asyncio
import logging
import sys

import httpx
from aiogram import Bot
from anthropic import AsyncAnthropic

from loxone_voice.adapter import (
    LoxoneClient,
    WebsocketsTransport,
    fetch_public_key,
    fetch_structure_file,
    parse_structure_file,
)
from loxone_voice.audit import AuditLog
from loxone_voice.config import Settings, get_settings
from loxone_voice.gateway import (
    Gateway,
    GatewayEngine,
    TelegramGateway,
    UserWhitelist,
    build_dispatcher,
    run_polling,
)
from loxone_voice.intent import (
    ConfirmationCallback,
    IntentEngine,
    build_default_toolset,
    build_system_prompt,
)

logger = logging.getLogger("loxone_voice")


def _configure_logging(level: str) -> None:
    logging.basicConfig(
        level=getattr(logging, level, logging.INFO),
        format="%(asctime)s %(levelname)-7s %(name)-32s %(message)s",
        stream=sys.stderr,
    )
    # Keep aiogram's signal-handling chatter at INFO; everything noisier
    # belongs to our app code.
    logging.getLogger("aiogram").setLevel(logging.INFO)
    logging.getLogger("httpx").setLevel(logging.WARNING)


def _build_engine_factory(
    *,
    settings: Settings,
    structure: object,
    anthropic: AsyncAnthropic,
    lox_client: LoxoneClient,
) -> object:
    """Return a factory that produces a per-user :class:`IntentEngine`."""
    system_prompt = build_system_prompt(structure)  # type: ignore[arg-type]
    tools = build_default_toolset(
        structure=structure,  # type: ignore[arg-type]
        state_store=lox_client.state,
        client=lox_client,
    )

    async def factory(_user_id: int, on_confirm: ConfirmationCallback) -> GatewayEngine:
        return IntentEngine(
            client=anthropic,  # type: ignore[arg-type]
            model=settings.anthropic_model,
            tools=tools,
            system_prompt=system_prompt,
            on_confirm=on_confirm,
        )

    return factory


async def _run(settings: Settings) -> None:
    logger.info("loxone-voice starting")
    logger.info("miniserver base URL: %s", settings.loxone_http_url)

    http = httpx.AsyncClient(verify=settings.loxone_verify_tls, timeout=15.0)
    try:
        # 1. Discover structure file via HTTP Basic Auth.
        raw_structure = await fetch_structure_file(
            http,
            base_url=settings.loxone_http_url,
            user=settings.loxone_user,
            password=settings.loxone_password.get_secret_value(),
        )
        structure = parse_structure_file(raw_structure)
        logger.info(
            "structure loaded: %d rooms, %d controls (%s)",
            len(structure.rooms),
            len(structure.controls),
            structure.miniserver_name or "no name",
        )

        # 2. Open the encrypted WebSocket session against the Miniserver.
        async def _fetch_public_key() -> str:
            return await fetch_public_key(http, base_url=settings.loxone_http_url)

        async def _connect_transport() -> WebsocketsTransport:
            return await WebsocketsTransport.connect(settings.loxone_ws_url)

        async with LoxoneClient(
            user=settings.loxone_user,
            password=settings.loxone_password.get_secret_value(),
            fetch_public_key=_fetch_public_key,
            connect_transport=_connect_transport,
        ) as lox_client:
            logger.info("miniserver handshake complete")

            # 3. Anthropic client + intent engine factory.
            anthropic = AsyncAnthropic(
                api_key=settings.anthropic_api_key.get_secret_value(),
            )
            engine_factory = _build_engine_factory(
                settings=settings,
                structure=structure,
                anthropic=anthropic,
                lox_client=lox_client,
            )

            # 4. Gateway + Telegram bot.
            audit = AuditLog(settings.audit_log_path)
            whitelist = UserWhitelist.from_iterable(settings.telegram_allowed_user_ids)
            if not whitelist:
                logger.warning("TELEGRAM_ALLOWED_USER_IDS is empty — no user can talk to the bot")

            gateway = Gateway(
                whitelist=whitelist,
                engine_factory=engine_factory,  # type: ignore[arg-type]
                audit=audit,
                channel="telegram",
            )

            bot = Bot(token=settings.telegram_bot_token.get_secret_value())
            tg_gateway = TelegramGateway(gateway)
            dispatcher = build_dispatcher(tg_gateway)

            logger.info("starting Telegram long-polling")
            await run_polling(bot, dispatcher)
    finally:
        await http.aclose()


def main() -> None:
    settings = get_settings()
    _configure_logging(settings.log_level)
    try:
        asyncio.run(_run(settings))
    except KeyboardInterrupt:
        logger.info("interrupted, shutting down")


if __name__ == "__main__":
    main()
