"""Tests for Synthetic Scenario Generator."""

import os
import json
import pytest
from engine.card import Card
from engine.ssg import build_mvbs, apply_conflict_logic, chaos_mode, run_card_scenario
from engine.game import Game
from admin.portal_logger import CRASH_REPORT_FILE

def test_mvbs_generation():
    """Verify MVBS gives players enough lands/creatures."""
    c = Card("Test Spell", "{1}{W}", "Instant", "Target creature gets +1/+1")
    g = build_mvbs(c)
    
    p1_board = [c for c in g.battlefield.cards if c.controller == g.players[0]]
    p2_board = [c for c in g.battlefield.cards if c.controller == g.players[1]]
    assert len(p1_board) >= 11  # 10 lands + 1 vanilla
    assert len(p2_board) >= 1   # 1 vanilla
    assert c in g.players[0].hand.cards  # Instants go to hand

def test_conflict_logic_injection():
    """Verify Rule 613 conflict cards are injected for continuous effects."""
    c = Card("Anthem", "{2}{W}", "Enchantment", "Creatures you control get +1/+1.")
    g = build_mvbs(c)
    apply_conflict_logic(c, g)
    
    # Check if Humility/Animator/Magus were added to P2's board
    p2_names = [card.name for card in g.battlefield.cards if card.controller == g.players[1]]
    assert "Humility" in p2_names
    assert "Animator" in p2_names
    assert "Magus of the Moon" in p2_names

def test_chaos_mode_fuzzing():
    import random
    rng = random.Random(42)  # Deterministic seed
    
    c = Card("Bear", "{1}{G}", "Creature", "")
    g = build_mvbs(c)
    chaos_mode(g, rng)
    
    # Should randomly add between 0 and 3 cards to P2
    p2_count = len([card for card in g.battlefield.cards if card.controller == g.players[1]])
    assert p2_count >= 1

def test_infinite_loop_crash_logging():
    """Verify that an infinite loop causes a FidelityCrash report."""
    # Ensure clean slate
    if os.path.exists(CRASH_REPORT_FILE):
        os.remove(CRASH_REPORT_FILE)
        
    # We will fake a card that creates an infinite loop on the stack
    loop_card = Card("Looper", "{0}", "Instant", "Infinite triggers")
    # Setting an effect that just keeps adding to the stack
    def loop_effect(gm, cd):
        from engine.game import StackItem
        gm.stack.cards.append(StackItem(loop_effect, cd, cd.controller, "Loop"))
        
    loop_card.effect = loop_effect
    loop_card.id = "loop_card_id"
    
    # Executing scenario should fail and log a crash
    success = run_card_scenario(loop_card, fuzz_seed=123)
    assert success is False
    
    # Check crash report
    assert os.path.exists(CRASH_REPORT_FILE)
    with open(CRASH_REPORT_FILE, "r") as f:
        reports = json.load(f)
        
    assert len(reports) > 0
    assert reports[-1]["crash_type"] == "SSG_EXECUTION_CRASH"
    assert "Infinite Loop Detected" in reports[-1]["message"]

def test_sba_audit_crash_logging():
    """Verify that an SBA invariant violation causes a FidelityCrash report."""
    # Ensure clean slate
    if os.path.exists(CRASH_REPORT_FILE):
        os.remove(CRASH_REPORT_FILE)
        
    g = build_mvbs(Card("Test", "{0}", "Artifact", ""))
    g.players[0].life = -5
    g.ssg_strict_mode = True
    
    # Bypass normal SBA checks to trigger the audit explicitly
    try:
        g._audit_sbas()
        failed = False
    except RuntimeError:
        failed = True
        
    assert failed is True
    
    assert os.path.exists(CRASH_REPORT_FILE)
    with open(CRASH_REPORT_FILE, "r") as f:
        reports = json.load(f)
        
    assert reports[-1]["crash_type"] == "SBA Audit Failure"
