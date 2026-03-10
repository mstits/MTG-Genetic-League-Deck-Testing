"""Web route modules for the MTG Genetic League dashboard.

Each module defines an APIRouter that gets included into the main FastAPI app.
This package splits the monolithic app.py into logical route groups:

    admin.py       — Admin portal, health checks, configuration, butterfly reports
    meta.py        — Metagame analytics: matchup matrix, trends, turn distribution
    simulation.py  — Simulation endpoints: test-deck, flex-test, gauntlet, mulligan
"""
