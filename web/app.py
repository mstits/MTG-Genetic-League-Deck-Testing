"""Web Dashboard — FastAPI application entry point.

This module initialises the FastAPI app, CORS, Jinja2 templates,
shared caches, WebSocket streaming, and wires in all route modules.

All endpoint implementations live in web/routes/:
    admin.py      — Admin portal, health, config, butterfly reports
    meta.py       — Matchup matrix, turn distribution, meta trends
    simulation.py — Test-deck, flex-test, mana-calc, gauntlet, salt
    decks.py      — Deck detail, suggestions, compare, export, browse
    views.py      — Dashboard, leaderboard, top-cards, meta overview,
                     match history, stats, matchups, matches, replay

Shared helpers live in web/helpers.py (rate limiter, decklist parser).
"""

from fastapi import FastAPI, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.templating import Jinja2Templates
import uvicorn
import os
import json
import logging
from data.db import get_db_connection
from starlette.middleware.cors import CORSMiddleware

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

# ─── Templates ────────────────────────────────────────────────────────────────
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
TEMPLATES_DIR = os.path.join(BASE_DIR, "templates")
if not os.path.exists(TEMPLATES_DIR):
    os.makedirs(TEMPLATES_DIR)

templates = Jinja2Templates(directory=TEMPLATES_DIR)

# Import canonical parse_cmc from optimizer (handles hybrid mana)
from optimizer.genetic import parse_cmc  # noqa: E402

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


# ─── Shared Caches ────────────────────────────────────────────────────────────
# These are imported by route modules via `from web.app import _get_card_pool`

_card_pool_cache = None


def _get_card_pool():
    """Load the full card pool once and cache at module level. Thread-safe via GIL."""
    global _card_pool_cache
    if _card_pool_cache is not None:
        return _card_pool_cache

    base = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    cp_path = os.path.join(base, 'data', 'legal_cards.json')
    if not os.path.exists(cp_path):
        cp_path = os.path.join(base, 'data', 'processed_cards.json')

    card_pool = {}
    with open(cp_path) as f:
        for c in json.load(f):
            name = c.get('name', '')
            card_pool[name] = c
            if ' // ' in name:
                front_face = name.split(' // ')[0].strip()
                if front_face not in card_pool:
                    card_pool[front_face] = c

    from engine.card_builder import inject_basic_lands
    inject_basic_lands(card_pool)

    _card_pool_cache = card_pool
    logger.info("Card pool cached: %d cards", len(card_pool))
    return card_pool


_card_search_cache = None


def _get_card_search_cache():
    """Load card names/metadata once for autocomplete. Thread-safe via GIL."""
    global _card_search_cache
    if _card_search_cache is not None:
        return _card_search_cache

    base = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    cp_path = os.path.join(base, 'data', 'legal_cards.json')
    if not os.path.exists(cp_path):
        cp_path = os.path.join(base, 'data', 'processed_cards.json')
    with open(cp_path) as f:
        raw = json.load(f)

    _card_search_cache = []
    for c in raw:
        name = c.get('name', '')
        _card_search_cache.append({
            'name': name,
            'name_lower': name.lower(),
            'mana_cost': c.get('mana_cost', ''),
            'type_line': c.get('type_line', ''),
            'colors': c.get('colors', []),
            'cmc': c.get('cmc', 0),
        })
    return _card_search_cache


# ─── Health Check ─────────────────────────────────────────────────────────────

@app.get("/health", response_class=JSONResponse)
async def health_check():
    """Health check endpoint for container orchestration (k8s probes).

    Returns:
        JSON with service status, DB connectivity, and cache state.
        200 if healthy, 503 if DB is unreachable.
    """
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
