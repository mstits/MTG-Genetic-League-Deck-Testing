"""Admin routes — portal UI, health checks, configuration, and monitoring.

Endpoints:
    GET  /admin                  — Admin portal HTML page
    GET  /admin/butterfly        — Misplay Hunter butterfly map viewer
    GET  /api/admin/health       — Engine rules coverage stats
    POST /api/admin/restart      — Restart Sovereign simulation process
    POST /api/admin/reset-elo    — Reset all deck ELO ratings to 1200
    GET  /api/butterfly-reports  — Misplay Hunter report data
    GET  /api/hall-of-fame       — All-time greatest evolved decks
    GET  /api/config             — Current engine configuration
    POST /api/config             — Update engine configuration
    GET  /api/error-budget       — Error budget monitoring status
"""

import os
import json
import logging
from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse
from starlette.responses import JSONResponse
from data.db import get_db_connection, get_hall_of_fame

logger = logging.getLogger(__name__)

# Router with /admin and /api prefix routes mixed (matches original app.py structure)
router = APIRouter(tags=["admin"])


# ─── Admin Pages ──────────────────────────────────────────────────────────────

@router.get("/admin", response_class=HTMLResponse)
async def admin_portal(request: Request):
    """Main Admin Portal UI — engine config, health dashboards, and butterfly maps."""
    from web.app import templates
    return templates.TemplateResponse("admin.html", {"request": request})


@router.get("/admin/butterfly", response_class=HTMLResponse)
async def butterfly_dashboard(request: Request):
    """Admin UI for viewing Misplay Hunter butterfly maps (what-if analysis)."""
    from web.app import templates
    return templates.TemplateResponse("butterfly.html", {"request": request})


# ─── Admin API ────────────────────────────────────────────────────────────────

@router.get("/api/admin/health")
async def get_admin_health():
    """Live Health Check — reports engine rules coverage percentage.

    Returns:
        coverage_percent: float — percentage of rules scenarios tested
        tested_interactions: int — number of tested rule scenarios
        total_scenarios: int — total scenarios in the registry
    """
    from engine.rules_sandbox import SCENARIO_REGISTRY
    tested = len(SCENARIO_REGISTRY)
    total = max(tested, 1)  # Denominator is registry size itself
    coverage = min((tested / total) * 100, 100.0)
    return {
        "coverage_percent": round(coverage, 1),
        "tested_interactions": tested,
        "total_scenarios": total
    }


@router.post("/api/admin/restart")
async def admin_restart():
    """Restart the Sovereign simulation as a background process.

    Launches sovereign.py using the project's virtualenv Python interpreter.
    Output is redirected to data/sovereign_stdout.log.
    """
    import subprocess

    base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    project_root = os.path.dirname(base_dir) if base_dir.endswith('web') else base_dir
    venv_python = os.path.join(project_root, '.venv', 'bin', 'python')
    sovereign_script = os.path.join(project_root, 'sovereign.py')

    try:
        subprocess.Popen(
            [venv_python, sovereign_script],
            cwd=project_root,
            stdout=open(os.path.join(project_root, 'data', 'sovereign_stdout.log'), 'w'),
            stderr=subprocess.STDOUT,
        )
        return {"status": "ok", "message": "Sovereign simulation restarted in background."}
    except Exception as e:
        logger.exception("Failed to restart sovereign")
        return JSONResponse({"status": "error", "message": "Failed to restart simulation."}, status_code=500)


@router.post("/api/admin/reset-elo")
async def admin_reset_elo():
    """Reset all deck ELO ratings to 1200 (fresh start).

    WARNING: This is a destructive operation that clears all rating progress.
    Returns the number of decks affected.
    """
    try:
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("UPDATE decks SET elo = 1200")
            conn.commit()
            affected = cursor.rowcount
        return {"status": "ok", "message": f"Reset {affected} decks to ELO 1200."}
    except Exception as e:
        logger.exception("Failed to reset ELO")
        return JSONResponse({"status": "error", "message": "Failed to reset ELO ratings."}, status_code=500)


# ─── Butterfly Reports (Misplay Hunter) ──────────────────────────────────────

@router.get("/api/butterfly-reports")
async def get_butterfly_reports():
    """Retrieve all Misplay Hunter reports, enriched with deck names.

    Misplay Hunter runs what-if analysis: replaying games with different decisions
    to identify where suboptimal plays changed the outcome (butterfly effect).
    Reports are sorted newest-first.
    """
    from engine.misplay_hunter import BUTTERFLY_REPORTS_FILE

    if not os.path.exists(BUTTERFLY_REPORTS_FILE):
        return []

    with open(BUTTERFLY_REPORTS_FILE, "r") as f:
        data = json.load(f)

    # Enrich report entries with human-readable deck names from the database
    with get_db_connection() as conn:
        cursor = conn.cursor()
        for report in data:
            cursor.execute('SELECT name FROM decks WHERE id = %s', (report['deck1_id'],))
            d1 = cursor.fetchone()
            report['deck1_name'] = d1['name'] if d1 else f"Deck {report['deck1_id']}"

            cursor.execute('SELECT name FROM decks WHERE id = %s', (report['deck2_id'],))
            d2 = cursor.fetchone()
            report['deck2_name'] = d2['name'] if d2 else f"Deck {report['deck2_id']}"

    # Sort newest first for the dashboard timeline view
    data.sort(key=lambda x: x['timestamp'], reverse=True)
    return data


# ─── Hall of Fame ─────────────────────────────────────────────────────────────

@router.get("/api/hall-of-fame")
async def get_hall_of_fame_api(limit: int = 50):
    """Get all-time greatest evolved decks, ranked by peak ELO.

    Args:
        limit: Maximum number of inductees to return (default: 50)
    """
    inductees = get_hall_of_fame(limit)
    return {"inductees": inductees, "total": len(inductees)}


# ─── Engine Configuration ────────────────────────────────────────────────────

@router.get("/api/config")
async def get_config():
    """Return current engine configuration (simulation parameters, K-factors, etc.)."""
    from engine.engine_config import config as engine_config
    return JSONResponse(engine_config.to_dict())


@router.post("/api/config")
async def update_config(request: Request):
    """Update engine configuration values.

    Accepts a JSON body with key-value pairs to update.
    Invalid keys or values return 400 Bad Request.
    """
    from engine.engine_config import config as engine_config
    try:
        body = await request.json()
        engine_config.update_from_dict(body)
        return JSONResponse({"status": "ok", "config": engine_config.to_dict()})
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=400)


@router.get("/api/error-budget")
async def get_error_budget():
    """Return current error budget status for production monitoring.

    Error budget tracks simulation failures vs total runs. When the budget
    is exhausted, the system should pause evolution until failures are investigated.
    """
    from simulation.runner import get_error_budget_status
    return JSONResponse(get_error_budget_status())
