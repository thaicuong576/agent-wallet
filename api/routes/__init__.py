"""Route modules for the BaKhi Wallet API."""

from api.routes.decisions import router as decisions_router
from api.routes.health import router as health_router
from api.routes.orders import router as orders_router
from api.routes.state import router as state_router
from api.routes.stop import router as stop_router
from api.routes.tier import router as tier_router

__all__ = [
    "decisions_router",
    "health_router",
    "orders_router",
    "state_router",
    "stop_router",
    "tier_router",
]
