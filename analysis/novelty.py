"""Novelty detection — Finds decks with unique strategies in the league.

Uses Jaccard similarity to compare each deck's card list against the
top 10 meta decks.  Decks with high ELO but low similarity to the
meta are flagged as "novel" — potential rogue strategies worth studying.
"""

import json
from data.db import get_db_connection


class NoveltyDetector:
    """Detects novel deck strategies by comparing card lists to the top meta.

    Novelty score = (1 - max_similarity_to_meta) × (elo / 1200).
    High novelty = unique card choices + strong competitive performance.
    """

    def get_deck_fingerprint(self, card_list: dict) -> set:
        """
        Returns a set of card names (ignoring lands?)
        """
        # We want to compare non-lands mostly?
        # Or just all cards.
        return set(card_list.keys())

    def calculate_jaccard_similarity(self, deck1_cards: dict, deck2_cards: dict) -> float:
        set1 = self.get_deck_fingerprint(deck1_cards)
        set2 = self.get_deck_fingerprint(deck2_cards)
        
        intersection = len(set1.intersection(set2))
        union = len(set1.union(set2))
        
        if union == 0: return 0.0
        return intersection / union

    def find_novel_decks(self, limit=10):
        """
        Finds decks with High Win Rate / Elo but Low Similarity to Top 10 Meta Decks.
        """
        with get_db_connection() as conn:
            # 1. Get Top 10 Meta Decks (by Elo/Wins)
            cursor = conn.cursor()
            cursor.execute('SELECT id, name, card_list, elo FROM decks WHERE active=1 ORDER BY elo DESC LIMIT 10')
            top_decks = [dict(row) for row in cursor.fetchall()]
            
            if not top_decks: return []
            
            # 2. Get all other winning decks (e.g. > 55% win rate)
            # Or just check top 100
            cursor.execute('SELECT id, name, card_list, elo, wins, losses FROM decks WHERE active=1 AND wins > 5 ORDER BY elo DESC LIMIT 100')
            candidates = [dict(row) for row in cursor.fetchall()]
            
        novel_decks = []
        
        for cand in candidates:
            # Skip if it is one of the top decks
            if any(cand['id'] == td['id'] for td in top_decks):
                continue
                
            cand_cards = json.loads(cand['card_list'])
            if isinstance(cand_cards, list): # Format hotfix if DB has legacy lists
                c = {}
                for n in cand_cards: c[n] = c.get(n, 0) + 1
                cand_cards = c
            
            # Calculate Max Similarity to any Meta Deck
            max_sim = 0.0
            for meta in top_decks:
                meta_cards = json.loads(meta['card_list'])
                if isinstance(meta_cards, list):
                    c = {}
                    for n in meta_cards: c[n] = c.get(n, 0) + 1
                    meta_cards = c
                    
                sim = self.calculate_jaccard_similarity(cand_cards, meta_cards)
                max_sim = max(max_sim, sim)
                
            # If similarity is low (e.g. < 0.5) but performant
            novelty_score = (1.0 - max_sim) * (cand['elo'] / 1200.0) # Boost by Elo
            cand['novelty_score'] = novelty_score
            cand['max_similarity'] = max_sim
            
            novel_decks.append(cand)
            
        # Sort by novelty
        novel_decks.sort(key=lambda x: x['novelty_score'], reverse=True)
        return novel_decks[:limit]

if __name__ == "__main__":
    nd = NoveltyDetector()
    novel = nd.find_novel_decks()
    print("--- Novelty Report ---")
    for d in novel:
        print(f"Deck: {d['name']} (Elo: {d['elo']:.0f}) | Similarity: {d['max_similarity']:.2f} | Score: {d['novelty_score']:.2f}")
