"""The HTTP surface, one module per thing being administered."""

from fastapi import APIRouter

from timothy_api.routers import (
    audit_log,
    client_logs,
    diagnostics,
    enforcement,
    events,
    exceptions,
    guilds,
    listings,
    notifications,
    ops,
    pools,
    subscriptions,
)

api = APIRouter()
"""Everything behind the service token. `/health` and `/openapi.json` stay outside it —
the compose healthcheck and the client generator both have to reach them."""

for router in (
    pools.router,
    listings.router,
    guilds.router,
    subscriptions.router,
    exceptions.router,
    notifications.router,
    enforcement.router,
    diagnostics.router,
    events.router,
    audit_log.router,
    ops.router,
    client_logs.router,
):
    api.include_router(router)
