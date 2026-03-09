"""SideboardAgent — Analyzes an opponent's deck and swaps cards optimally.

Used between games in a Bo3 match. Inspects the opponent's previous decklist
to identify their archetype, adjusts the valuation of the player's maindeck
and sideboard cards, and swaps the lowest-value maindeck cards for the
highest-value sideboard cards.
"""

from typing import List, Tuple
from engine.deck import Deck
from engine.card import Card


class SideboardAgent:
    """Intelligently sideboards based on opponent's deck profile."""

    def __init__(self, my_deck: Deck):
        self.deck = my_deck

    def sideboard_against(self, opp_deck: Deck):
        """Analyze opp_deck and swap cards in my_deck's blueprints/sideboard."""
        if not self.deck.sideboard:
            return  # Nothing to swap
            
        # 1. Profile Opponent
        opp_creatures = sum(1 for c in opp_deck.maindeck if c.is_creature)
        opp_spells = sum(1 for c in opp_deck.maindeck if not c.is_creature and not c.is_land)
        
        has_graveyard = any(
            'return' in (c.oracle_text or '').lower() and 'graveyard' in (c.oracle_text or '').lower()
            for c in opp_deck.maindeck
        )
        
        has_artifacts = sum(1 for c in opp_deck.maindeck if 'Artifact' in c.type_line) > 5
        has_enchants = sum(1 for c in opp_deck.maindeck if 'Enchantment' in c.type_line) > 5
        
        is_aggro = opp_creatures >= 20
        is_control = opp_spells >= 15 and opp_creatures <= 10
        
        # 2. Score my maindeck cards (lower is worse)
        # We need to access individual blueprint copies to swap them out
        maindeck_candidates = []
        for i, (card, qty) in enumerate(self.deck._blueprints):
            if card.is_land:
                continue  # Never cut lands during automated sideboarding for safety
            
            score = self._score_card(card, is_aggro, is_control, has_graveyard, has_artifacts, has_enchants)
            maindeck_candidates.append({
                'index': i,
                'card': card,
                'qty': qty,
                'score': score
            })
            
        if not maindeck_candidates:
            return
            
        # 3. Score my sideboard cards (higher is better)
        sb_candidates = []
        for i, card in enumerate(self.deck.sideboard):
            score = self._score_card(card, is_aggro, is_control, has_graveyard, has_artifacts, has_enchants)
            sb_candidates.append({
                'index': i,
                'card': card,
                'score': score
            })
            
        # 4. Find the weakest maindeck cards and strongest sideboard cards
        # Flatten maindeck candidates into individual 'slots' we can cut
        cuttable_slots = []
        for cand in maindeck_candidates:
            for _ in range(cand['qty']):
                cuttable_slots.append({'bp_index': cand['index'], 'score': cand['score']})
                
        cuttable_slots.sort(key=lambda x: x['score'])  # Worst first
        sb_candidates.sort(key=lambda x: x['score'], reverse=True)  # Best first
        
        # 5. Swap as long as SB score > Maindeck score + threshold
        swaps = 0
        THRESHOLD = 1.0  # Only swap if it's a meaningful upgrade
        
        # We process swaps by rebuilding the blueprint dictionary
        new_blueprints = {i: qty for i, (_, qty) in enumerate(self.deck._blueprints)}
        new_sideboard = list(self.deck.sideboard)
        
        swaps_log = []
        for i in range(min(len(cuttable_slots), len(sb_candidates))):
            md_slot = cuttable_slots[i]
            sb_slot = sb_candidates[i]
            
            if sb_slot['score'] > md_slot['score'] + THRESHOLD:
                # Execute Swap
                bp_idx = md_slot['bp_index']
                new_blueprints[bp_idx] -= 1
                
                # The card entering the maindeck
                incoming_card = self.deck.sideboard[sb_slot['index']]
                
                # Retrieve the outgoing card's name
                outgoing_card, _ = self.deck._blueprints[bp_idx]
                
                # Check if it already exists in blueprints to increment
                found = False
                for j, (c, _) in enumerate(self.deck._blueprints):
                    if c.name == incoming_card.name:
                        new_blueprints[j] = new_blueprints.get(j, 0) + 1
                        found = True
                        break
                        
                if not found:
                    self.deck._blueprints.append((incoming_card, 0))
                    new_blueprints[len(self.deck._blueprints)-1] = 1
                
                swaps += 1
                swaps_log.append({
                    'card_in': incoming_card.name,
                    'card_out': outgoing_card.name
                })

        if swaps > 0:
            # Reconstruct blueprints
            final_blueprints = []
            for i, (c, _) in enumerate(self.deck._blueprints):
                if new_blueprints.get(i, 0) > 0:
                    final_blueprints.append((c, new_blueprints[i]))
            self.deck._blueprints = final_blueprints
            
        return swaps_log


    def _score_card(self, card: Card, is_aggro: bool, is_control: bool, 
                    has_gy: bool, has_arts: bool, has_enchts: bool) -> float:
        """Assign a situational score to a card against this specific opponent."""
        score = 5.0  # Base line
        text = (card.oracle_text or '').lower()
        
        if is_aggro:
            if card.is_board_wipe or 'destroy all creatures' in text or 'each creature' in text: score += 4.0
            if 'gain' in text and 'life' in text: score += 2.0
            if card.is_creature and card.base_toughness and card.base_toughness >= 3: score += 1.0
            if card.cost and any(c.isdigit() and int(c) >= 5 for c in card.cost): score -= 3.0 # Too slow
            
        if is_control:
            if card.is_counter: score += 3.0
            if 'can\'t be countered' in text: score += 3.0
            if card.is_board_wipe: score -= 3.0 # Dead card against control
            if card.is_removal and 'creature' in text: score -= 2.0 # Dead card
            
        if has_gy:
            if 'exile player\'s graveyard' in text or 'exile target graveyard' in text or 'exile all graveyards' in text: score += 5.0
            if 'exile target card from a graveyard' in text: score += 2.0
            
        if has_arts and ('destroy' in text and 'artifact' in text):
            score += 4.0
            
        if has_enchts and ('destroy' in text and 'enchantment' in text):
            score += 4.0
            
        return score
