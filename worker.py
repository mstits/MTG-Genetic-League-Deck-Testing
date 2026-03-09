"""SimWorker — Distributed Redis Queue Worker for MTG Genetic League.
Runs headless simulations pushed by the FastAPI orchestrator.
"""

import os
import json
import redis
from rq import Worker, Queue, Connection
from simulation.parallel import load_card_pool_global, run_match_task

redis_url = os.environ.get("REDIS_URL", "redis://localhost:6379")
conn = redis.from_url(redis_url)

if __name__ == '__main__':
    # Pre-load card pool into memory so the fork doesn't have to read JSON every time
    print("Loading global card pool for worker...")
    load_card_pool_global()
    
    print(f"Starting SimWorker. Connected to {redis_url}")
    with Connection(conn):
        worker = Worker(['mtg-sims'])
        worker.work()
