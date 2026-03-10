"""Synthetic Scenario Generator (SSG).

Generates minimum viable board states for every card,
forces layering conflicts (Rule 613), verifies SBA invariants,
and applies Fuzzing mechanics (Chaos Mode) for stress-testing.
"""

import os
import json
import random
import traceback
import logging
from typing import List, Dict

logger = logging.getLogger(__name__)

from engine.card import Card
from engine.player import Player
from engine.deck import Deck
from engine.game import Game
from admin.portal_logger import log_fidelity_crash


def _land() -> Card:
    return Card(
        name="Plains",
        cost="",
        type_line="Basic Land — Plains",
        oracle_text="{T}: Add {W}.",
        produced_mana=["W"]
    )

def _deck() -> Deck:
    d = Deck()
    for _ in range(60):
        d.add_card(_land(), 1)
    return d

def _game(strict=True) -> Game:
    g = Game([Player("P1", _deck()), Player("P2", _deck())])
    g.turn_count = 1
    g.ssg_strict_mode = strict
    return g

def build_mvbs(card: Card) -> Game:
    """Creates a Minimal Viable Board State for the given card."""
    g = _game()
    p1 = g.players[0]
    p2 = g.players[1]
    
    # Give P1 plenty of mana and generic targets
    for _ in range(10):
        land = _land()
        land.controller = p1
        g.battlefield.add(land)
        
    vanilla_p1 = Card("Vanilla P1", "{1}", type_line="Creature", base_power=2, base_toughness=2, oracle_text="")
    vanilla_p1.controller = p1
    g.battlefield.add(vanilla_p1)
    
    vanilla_p2 = Card("Vanilla P2", "{1}", type_line="Creature", base_power=2, base_toughness=2, oracle_text="")
    vanilla_p2.controller = p2
    g.battlefield.add(vanilla_p2)
    
    # Place target card on P1's battlefield or hand
    card.controller = p1
    if "Instant" in card.type_line or "Sorcery" in card.type_line:
        p1.hand.add(card)
    else:
        g.battlefield.add(card)
        
    return g

def apply_conflict_logic(card: Card, g: Game):
    """Forces Rule 613 Layering conflicts by spawning opposing Continuous Effects."""
    conflict_keywords = [
        "gets +", "gets -", "has flying", "loses all abilities", 
        "base power and toughness", "is a", "protection from"
    ]
    
    # Check if card modifies characteristics
    text = (card.oracle_text or "").lower()
    if any(k in text for k in conflict_keywords) or card.is_enchantment or card.is_artifact:
        p2 = g.players[1]
        
        # Inject Humility to strip abilities and set P/T
        humility = Card(
            name="Humility",
            cost="{2}{W}{W}",
            type_line="Enchantment",
            oracle_text="All creatures lose all abilities and have base power and toughness 1/1."
        )
        humility.controller = p2
        g.battlefield.add(humility)
        
        # Inject an Opalescence-like animator
        animator = Card(
            name="Animator",
            cost="{2}{W}",
            type_line="Enchantment",
            oracle_text="Each other non-Aura enchantment is a creature with power and toughness each equal to its mana value."
        )
        animator.controller = p2
        g.battlefield.add(animator)
        
        # Inject a characteristic defining ability override
        magus = Card(
            name="Magus of the Moon",
            cost="{2}{R}",
            type_line="Creature — Human Wizard",
            base_power=2, base_toughness=2,
            oracle_text="Nonbasic lands are Mountains."
        )
        magus.controller = p2
        g.battlefield.add(magus)

def chaos_mode(g: Game, rng: random.Random):
    """Fuzzing Engine: injects Stasis, Humility, Teferi arbitrarily."""
    injects = [
        Card("Stasis", "{1}{U}", "Enchantment", "Players skip their untap steps."),
        Card("Humility", "{2}{W}{W}", "Enchantment", "All creatures lose all abilities and have base power and toughness 1/1."),
        Card("Teferi, Time Raveler", "{1}{W}{U}", "Legendary Planeswalker — Teferi", "Each opponent can cast spells only any time they could cast a sorcery.\n+1: Until your next turn, you may cast sorcery spells as though they had flash.\n-3: Return up to one target artifact, creature, or enchantment to its owner's hand. Draw a card.", loyalty=4)
    ]
    
    num_to_inject = rng.randint(0, 3)
    chosen = rng.sample(injects, num_to_inject)
    
    p2 = g.players[1]
    for c in chosen:
        # Clone before adding so we get unique instances
        import copy
        cc = copy.deepcopy(c)
        cc.controller = p2
        g.battlefield.add(cc)

def run_card_scenario(card: Card, fuzz_seed: int = 42) -> bool:
    """Runs MVBS + Conflict + Chaos + Strict SBA for a single card."""
    g = None
    try:
        g = build_mvbs(card)
        apply_conflict_logic(card, g)
        chaos_mode(g, random.Random(fuzz_seed))
        
        # Resolve any ETB or state-based triggers from the initial board setup
        g.check_state_based_actions()
        while len(g.stack) > 0:
            g._resolve_stack_top()
        
        # Try casting the card if it's in hand (Instant/Sorcery)
        if card in g.players[0].hand.cards:
            # We don't have targeting logic in SSG directly, so we use dummy resolution if it has effect
            if hasattr(card, 'effect') and card.effect:
                # Force onto stack
                g.players[0].hand.remove(card)
                g.stack.add(card)
                
        # Resolve again
        while len(g.stack) > 0:
            g._resolve_stack_top()
            
        g.check_state_based_actions()
        return True
    except Exception as e:
        log_fidelity_crash(
            card_id=card.id if hasattr(card, "id") else card.name,
            crash_type="SSG_EXECUTION_CRASH",
            message=f"Crash during SSG execution: {e}",
            game_state={"turn": g.turn_count, "stack_size": len(g.stack)} if g else {},
            exc=e
        )
        return False

def run_ssg_suite(limit: int = 100):
    """Runs the Synthetic Scenario Generator across the card database."""
    card_db_path = os.path.join(
        os.path.dirname(os.path.dirname(__file__)),
        "data", "legal_cards.json"
    )
    
    logger.info("LOADING DB: %s", card_db_path)
    if not os.path.exists(card_db_path):
        logger.error("legal_cards.json not found.")
        return
        
    with open(card_db_path, "r") as f:
        cards_data = json.load(f)
        
    passed = 0
    failed = 0
    
    # Process up to 'limit' cards
    for idx, cdata in enumerate(cards_data[:limit]):
        if "name" not in cdata: continue
        
        c = Card(
            name=cdata.get("name"),
            cost=cdata.get("mana_cost", ""),
            type_line=cdata.get("type_line", ""),
            oracle_text=cdata.get("oracle_text", ""),
            base_power=cdata.get("power"),
            base_toughness=cdata.get("toughness")
        )
        c.id = cdata.get("id", f"c_{idx}")
        
        # Run with a random seed
        success = run_card_scenario(c, fuzz_seed=idx)
        if success:
            passed += 1
        else:
            failed += 1
            
    logger.info("SSG SUITE COMPLETE: %d passed, %d failed (crashed).", passed, failed)
    if failed > 0:
        logger.warning("Check admin/admin_crash_reports.json for details.")

if __name__ == "__main__":
    run_ssg_suite(500)
