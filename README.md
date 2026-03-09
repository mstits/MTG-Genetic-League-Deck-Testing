# MTG Genetic League - Turnkey Deployment

This README outlines the turnkey `vibe-start` process for deploying the MTG Genetic League Application using Docker Compose.

## Prerequisites

- Docker and Docker Compose installed on your system.

## v1.0 Features Overview (The 100% Core)

The MTG Genetic League engine is now fully functional, heavily tested, and verified against Comprehensive Rules edge-cases.

- **Rules Sandbox**: 100 automated interaction smoke-tests covering layers, triggers, SBAs, and SBA loops.
- **Genetic Engine (v2)**: Advanced Evolution powered by a Novelty Search Fitness Equation $F = (WR \times 0.7) + (Nov \times 0.3)$ using **Milvus Vector DB** fingerprint clustering.
- **Distributed Architecture**: Multi-node scalability pushing headless simulation matches across **Redis Message Queues (RQ)** and logging to a central **PostgreSQL** data backend.
- **MCTS Pro-Level AI**: `MCTSAgent` leverages Monte Carlo Tree rollouts parallelizing tactical combinations 2-3 turns deeply for high-level competitive Boss battles.
- **Misplay Hunter**: Strategic butterfly maps built natively into the Admin UI to catch system upsets.
- **Admin War Room**: Fully functional environment tuning with live UI resource constraints, a dynamic overarching **Format Toggle** (Standard, Commander Bracket, Modern), and an interactive **Topological Meta-Map**.

## Quick Start (vibe-start)

The environment has been automated and requires zero manual setups.

1. Start the services:

   ```bash
   pip install -r requirements.txt
   docker-compose up -d --build
   ```

2. The application will be live at `http://localhost:8000`.
3. Check the Engine Room / Admin Portal at `http://localhost:8000/admin`.

## System Diagnostics

Once deployed, verify operations by navigating to the **Admin Portal** and checking the "Live Health Check" ring. It executes a real-time smoke test of the Comprehensive Rules sandbox to guarantee Fidelity Report integrity.

You can also test the distributed network queues natively by triggering a headless standard run via `python scripts/test_cluster.py`.
