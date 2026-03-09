"""Tests for Misplay Hunter & Monte Carlo Analytics."""

import os
import json
import pytest
from engine.card import Card
from engine.player import Player
from engine.deck import Deck
from engine.game import Game
from engine.misplay_hunter import MisplayHunter, BUTTERFLY_REPORTS_FILE
from simulation.runner import SimulationRunner
from agents.heuristic_agent import HeuristicAgent

def build_simple_game():
    c1 = Card("Mountain", "", "Land", "{T}: Add {R}")
    c2 = Card("Bolt", "{R}", "Instant", "Deal 3 damage")
    
    d1 = Deck(); d1.add_card(c1, 10); d1.add_card(c2, 10)
    d2 = Deck(); d2.add_card(c1, 10); d2.add_card(c2, 10)
    
    p1 = Player("P1", d1)
    p2 = Player("P2", d2)
    return Game([p1, p2])

def test_misplay_hunter_detects_upsets():
    hunter = MisplayHunter()
    
    # High ELO (1500) loses to Low ELO (1200) -> UPSET
    assert hunter.detect_upset(1500, 1200, 1) is True
    
    # Low ELO (1200) loses to High (1500) -> Normal game
    assert hunter.detect_upset(1200, 1500, 1) is False
    
    # Equal ELO -> Not an upset
    assert hunter.detect_upset(1300, 1300, 0) is False

def test_snapshot_capture_in_runner():
    g = build_simple_game()
    runner = SimulationRunner(g, [HeuristicAgent(), HeuristicAgent()], capture_snapshots=True)
    
    # Quick dirty game ending (player 1 loses fast)
    g.players[0].life = 0 
    res = runner.run()
    
    # Verify GameResult has snapshots list properly
    assert hasattr(res, 'snapshots')

def test_pivot_point_calculation(monkeypatch):
    # We fake snapshots 
    g = build_simple_game()
    hunter = MisplayHunter()
    
    # Very contrived "high chance of winning" to "zero chance"
    snapshots = [
        {"turn": 1, "agent_index": 0, "game_state": g.clone()},
        {"turn": 2, "agent_index": 0, "game_state": g.clone()}
    ]
    
    # P1 loses game immediately in snapshot 2
    snapshots[1]['game_state'].players[0].life = 0
    
    # Mock _mc_rollout so the test is deterministic and doesn't rely on actual heuristics
    def mock_rollout(game_state, idx, num_games=5):
        if game_state.players[0].life <= 0:
            return 0.0
        return 1.0
        
    monkeypatch.setattr(hunter, '_mc_rollout', mock_rollout)
    
    pivot = hunter._find_pivot_point(snapshots, high_elo_idx=0)
    assert pivot is not None
    assert pivot['turn'] == 2

def test_butterfly_report_generation():
    if os.path.exists(BUTTERFLY_REPORTS_FILE):
        os.remove(BUTTERFLY_REPORTS_FILE)
        
    hunter = MisplayHunter()
    
    hunter._generate_butterfly_report(
        high_elo_idx=0,
        deck1_id=1,
        deck2_id=2,
        pivot_turn=14,
        actual_action="Play Swamp",
        golden_action="Cast Damnation",
        winrate_diff=0.35,
        vibe_score=35.0
    )
    
    assert os.path.exists(BUTTERFLY_REPORTS_FILE)
    
    with open(BUTTERFLY_REPORTS_FILE, "r") as f:
        data = json.load(f)
        
    assert len(data) == 1
    assert data[0]["deck1_id"] == 1
    assert data[0]["golden_action"] == "Cast Damnation"
    assert data[0]["vibe_score"] == 35.0
