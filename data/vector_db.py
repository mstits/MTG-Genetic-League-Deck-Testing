"""Milvus Vector Database for Deck Fingerprint clustering.

Maintains a continuous topological map of the metagame. Decks are inserted as
128-dimensional dense vectors (produced via stable feature hashing of card names).

Exposes `get_novelty_score` for the upcoming Genetic Algorithm phase to reward
decks exploring uncharted topological space.
"""

import os
import hashlib
from pymilvus import MilvusClient

# Ensure the parent directory for the local db exists
import logging
logger = logging.getLogger(__name__)

MILVUS_PATH = os.environ.get("MILVUS_PATH", os.path.join(os.path.dirname(__file__), "milvus_fingerprints.db"))
client = MilvusClient(MILVUS_PATH)

COLLECTION_NAME = "deck_fingerprints"
DIM = 128

def init_vector_db():
    """Create the collection if it doesn't exist."""
    if not client.has_collection(COLLECTION_NAME):
        client.create_collection(
            collection_name=COLLECTION_NAME,
            dimension=DIM,
            metric_type="L2",
            auto_id=False
        )
        logger.info(f"Initialized Milvus vector collection: {COLLECTION_NAME} (DIM={DIM})")

def deck_to_vector(card_map: dict) -> list[float]:
    """Convert a MTG deck dict (name -> count) to a 128-dim normalized dense vector via feature hashing."""
    vec = [0.0] * DIM
    for card_name, count in card_map.items():
        # Hash the card name to an index
        idx = int(hashlib.md5(card_name.encode('utf-8')).hexdigest(), 16) % DIM
        vec[idx] += float(count)
    
    # L2 Normalize
    norm = sum(v**2 for v in vec) ** 0.5
    if norm > 0:
        vec = [v/norm for v in vec]
    return vec

def insert_deck_fingerprint(deck_id: int, card_map: dict):
    """Insert or update a deck's topological fingerprint in the Vector DB."""
    vector = deck_to_vector(card_map)
    data = [{"id": deck_id, "vector": vector}]
    client.upsert(
        collection_name=COLLECTION_NAME,
        data=data
    )

def get_novelty_score(card_map: dict, k: int = 5) -> float:
    """Calculate the average L2 distance to the k-nearest neighbors.
    Higher score = highly novel deck (uncharted space).
    Lower score = highly derivative deck (clustered).
    """
    if not client.has_collection(COLLECTION_NAME):
        return 0.0
        
    try:
        # Check if collection is empty using stats
        stats = client.get_collection_stats(COLLECTION_NAME)
        if stats.get('row_count', 0) == 0:
            return 1.0  # Maximum novelty if first deck
    except Exception:
        pass
        
    vector = deck_to_vector(card_map)
    res = client.search(
        collection_name=COLLECTION_NAME,
        data=[vector],
        limit=k,
        search_params={"metric_type": "L2"},
        output_fields=["id"]
    )
    
    if not res or not res[0]:
        return 1.0 # No neighbors found
        
    # Average distance to the nearest K networks
    distances = [hit["distance"] for hit in res[0]]
    avg_distance = sum(distances) / len(distances)
    
    # Since vectors are exactly L2 normalized (length 1), max L2 distance is 2.0 (opposite vectors).
    # We can normalize the novelty score to 0.0 - 1.0 range based on 2.0 max.
    return min(1.0, avg_distance / 2.0)

if __name__ == "__main__":
    init_vector_db()
