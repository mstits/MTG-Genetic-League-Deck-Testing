"""Web Dashboard — FastAPI application entry point.

This module initialises the FastAPI app, CORS, WebSocket streaming,
and wires in all route modules.

Shared state (templates, card-pool caches) lives in web/cache.py to
avoid circular imports between app.py and route modules.

Route modules (web/routes/):
    admin.py      — Admin portal, health, config, butterfly reports
    meta.py       — Matchup matrix, turn distribution, meta trends
    simulation.py — Test-deck, flex-test, mana-calc, gauntlet, salt
    decks.py      — Deck detail, suggestions, compare, export, browse
    views.py      — Dashboard, leaderboard, top-cards, meta overview,
                     match history, stats, matchups, matches, replay

Shared helpers: web/helpers.py (rate limiter, decklist parser)
Shared caches:  web/cache.py  (templates, card pool, card search)
"""

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import JSONResponse
import uvicorn
import os
import logging
from data.db import get_db_connection
from starlette.middleware.cors import CORSMiddleware

# Shared state — accessed by route modules via `from web.cache import ...`
from web.cache import templates, get_card_pool, get_card_search_cache  # noqa: F401

app = FastAPI(title="MTG Genetic League", description="AI-powered deck evolution dashboard")

logger = logging.getLogger(__name__)

# ─── CORS ─────────────────────────────────────────────────────────────────────
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:8000",
        "http://localhost:3000",
        os.getenv("CORS_ORIGIN", ""),
    ],
    allow_methods=["GET", "POST"],
    allow_headers=["*"],
)

# ─── Route Modules ────────────────────────────────────────────────────────────
from web.routes.admin import router as admin_router       # noqa: E402
from web.routes.meta import router as meta_router         # noqa: E402
from web.routes.simulation import router as simulation_router  # noqa: E402
from web.routes.decks import router as decks_router       # noqa: E402
from web.routes.views import router as views_router       # noqa: E402

app.include_router(admin_router)
app.include_router(meta_router)
app.include_router(simulation_router)
app.include_router(decks_router)
app.include_router(views_router)


# ─── Health Check ─────────────────────────────────────────────────────────────

@app.get("/health", response_class=JSONResponse)
async def health_check():
    """Health check endpoint for container orchestration (k8s probes).

    Returns:
        JSON with service status, DB connectivity, and cache state.
        200 if healthy, 503 if DB is unreachable.
    """
    from web.cache import _card_pool_cache  # noqa: F401
    status = {"service": "ok", "db": "unknown", "card_pool_cached": _card_pool_cache is not None}
    http_code = 200
    try:
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT 1")
            status["db"] = "connected"
    except Exception as e:
        logger.warning("Health check: DB unreachable: %s", e)
        status["db"] = "unreachable"
        status["service"] = "degraded"
        http_code = 503
    return JSONResponse(status, status_code=http_code)


# ─── WebSocket: Real-Time ELO Streaming ──────────────────────────────────────

_ws_clients: list[WebSocket] = []


@app.websocket("/ws/elo-stream")
async def elo_stream(websocket: WebSocket):
    """Stream live ELO updates as matches complete.

    Clients connect and receive JSON messages with deck ELO deltas.
    """
    await websocket.accept()
    _ws_clients.append(websocket)
    try:
        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        _ws_clients.remove(websocket)
    except Exception as e:
        logger.debug("WebSocket connection error: %s", e)
        if websocket in _ws_clients:
            _ws_clients.remove(websocket)


async def broadcast_elo_update(data: dict):
    """Push ELO update to all connected WebSocket clients."""
    disconnected = []
    for ws in _ws_clients:
        try:
            await ws.send_json(data)
        except Exception as e:
            logger.debug("WebSocket send failed: %s", e)
            disconnected.append(ws)
    for ws in disconnected:
        _ws_clients.remove(ws)


# ─── Entry Point ──────────────────────────────────────────────────────────────

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8000)
