"""Web Dashboard — FastAPI application entry point.

Pure wiring: creates the app, configures CORS, mounts routers, and
provides the WebSocket for live ELO streaming. Nothing else.

Shared state lives in web/cache.py (templates, card pool, search cache).
Route modules live in web/routes/ (admin, meta, simulation, decks, views).
Shared helpers live in web/helpers.py (rate limiter, decklist parser).
"""

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
import uvicorn
import os
import logging
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


# ─── WebSocket: Real-Time ELO Streaming ──────────────────────────────────────

_ws_clients: list[WebSocket] = []


@app.websocket("/ws/elo-stream")
async def elo_stream(websocket: WebSocket):
    """Stream live ELO updates as matches complete."""
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
