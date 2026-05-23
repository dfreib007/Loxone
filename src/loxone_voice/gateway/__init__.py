"""Channel-agnostic gateway and user-facing message routing."""

from .confirmation import ConfirmationGate, PendingAction
from .core import EngineFactory, Gateway, GatewayEngine, GatewayResponse
from .telegram_bot import TelegramGateway, build_dispatcher, run_polling
from .whitelist import UserWhitelist, WhitelistViolation

__all__ = [
    "ConfirmationGate",
    "EngineFactory",
    "Gateway",
    "GatewayEngine",
    "GatewayResponse",
    "PendingAction",
    "TelegramGateway",
    "UserWhitelist",
    "WhitelistViolation",
    "build_dispatcher",
    "run_polling",
]
