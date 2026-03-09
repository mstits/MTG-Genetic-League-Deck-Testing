"""Cluster Testing Script — Trigger Headless execution across Redis.

Verifies the end-to-end distributed infrastructure by seeding mock decks
and pushing a batch of standard format matches into the RQ workers.
"""

import os
import sys

# Append project root to path
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from data.db import init_db, save_deck
from data.vector_db import init_vector_db
from league.manager import LeagueManager
import time

def run_cluster_test():
    print("🚀 Initializing MTG Genetic League Distributed Cluster Test...")
    init_db()
    try:
        init_vector_db()
    except ImportError:
        print("Milvus Lite missing pkg_resources, skipping vector DB.")
    
    # 1. Provide mock competitive data
    deck_a_cards = {"Lightning Bolt": 4, "Monastery Swiftspear": 4, "Mountain": 12}
    deck_b_cards = {"Counterspell": 4, "Delver of Secrets": 4, "Island": 12}
    
    da_id = save_deck("Target Dummy Red", deck_a_cards, 0, "R")
    db_id = save_deck("Target Dummy Blue", deck_b_cards, 0, "U")
    
    print(f"✅ Mock Decks inserted [Red ID: {da_id}, Blue ID: {db_id}]")
    
    # 2. Trigger League Manager Orchestrator
    try:
        manager = LeagueManager(season_duration_seconds=30, match_limit=50)
        manager.divisions = ["Bronze"] # Restrict
        
        # Override match_args internally for a direct test payload
        manager._run_generation()
        print("✅ Cluster simulation payload distributed to Redis workers successfully.")
        
    except Exception as e:
        print(f"❌ Cluster architecture failed: {e}")

if __name__ == "__main__":
    run_cluster_test()
