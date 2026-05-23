"""Channel-agnostic gateway and user-facing message routing."""

from .core import EngineFactory, Gateway, GatewayEngine, GatewayResponse
from .telegram_bot import TelegramGateway, build_dispatcher, run_polling
from .whitelist import UserWhitelist, WhitelistViolation

__all__ = [
    "EngineFactory",
    "Gateway",
    "GatewayEngine",
    "GatewayResponse",
    "TelegramGateway",
    "UserWhitelist",
    "WhitelistViolation",
    "build_dispatcher",
    "run_polling",
]
