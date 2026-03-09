"""Tests for Phases 9-12: Alpha-strike, archetype classifier, PW targeting, mana efficiency.

Run with: python3 -m pytest tests/test_advanced.py -v
"""

import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from engine.card import Card
from engine.deck import Deck
from engine.game import Game
from engine.player import Player
from agents.heuristic_agent import HeuristicAgent
from engine.archetype_classifier import classify_deck


# ─── Archetype Classifier Tests ──────────────────────────────────────────

def test_classify_burn_as_aggro():
    """Burn decks with cheap spells should classify as Aggro."""
    deck = {
        'Lightning Bolt': 4, 'Shock': 4, 'Lava Spike': 4,
        'Goblin Guide': 4, 'Monastery Swiftspear': 4,
        'Mountain': 20
    }
    result = classify_deck(deck)
    assert result['archetype'] == 'Aggro', f"Expected Aggro, got {result['archetype']}"
    assert result['confidence'] > 0.5


def test_classify_control():
    """Counter-heavy decks should classify as Control."""
    deck = {
        'Counterspell': 4, 'Cryptic Command': 4, 'Wrath of God': 3,
        'Snapcaster Mage': 4, 'Opt': 4,
        'Island': 24, 'Plains': 2
    }
    result = classify_deck(deck)
    assert result['archetype'] == 'Control', f"Expected Control, got {result['archetype']}"


def test_classify_empty_deck():
    """Empty deck should return Unknown."""
    result = classify_deck({})
    assert result['archetype'] == 'Unknown'
    assert result['confidence'] == 0.0


def test_classifier_returns_all_fields():
    """Classifier should return archetype, confidence, scores, and signals."""
    deck = {'Mountain': 24, 'Lightning Bolt': 4}
    result = classify_deck(deck)
    assert 'archetype' in result
    assert 'confidence' in result
    assert 'scores' in result
    assert 'signals' in result
    assert isinstance(result['scores'], dict)


# ─── Threat Score Tests ──────────────────────────────────────────────────

def test_threat_score_basic():
    """A vanilla 3/3 should score 3.0."""
    card = Card("Test Creature", "2G", "Creature — Beast")
    card.power = 3
    card.toughness = 3
    score = Card._threat_score(card)
    assert score == 3.0, f"Expected 3.0, got {score}"


def test_threat_score_flying():
    """Flying should add +2 to threat score."""
    card = Card("Flyer", "2U", "Creature — Bird")
    card.power = 2
    card.toughness = 2
    card.has_flying = True
    score = Card._threat_score(card)
    assert score == 4.0, f"Expected 4.0, got {score}"


def test_threat_score_multi_keyword():
    """Multiple keywords should stack."""
    card = Card("Multi", "3W", "Creature — Angel")
    card.power = 4
    card.toughness = 4
    card.has_flying = True
    card.has_lifelink = True
    card.has_first_strike = True
    score = Card._threat_score(card)
    # 4 (power) + 2 (flying) + 1.5 (lifelink) + 1 (first strike) = 8.5
    assert score == 8.5, f"Expected 8.5, got {score}"


# ─── Player available_mana Test ──────────────────────────────────────────

def test_available_mana():
    """available_mana should count untapped lands controlled by the player."""
    # Build minimal decks
    deck_cards = [Card("Mountain", "", "Land") for _ in range(40)]
    p1_deck = Deck()
    for c in deck_cards[:20]:
        p1_deck.add_card(c)
    p2_deck = Deck()
    for c in deck_cards[20:]:
        p2_deck.add_card(c)
    
    p1 = Player("Test", p1_deck)
    p2 = Player("Opp", p2_deck)
    
    game = Game([p1, p2])
    
    # Put 3 untapped mountains on the battlefield
    for i in range(3):
        land = Card("Mountain", "", "Land")
        land.controller = p1
        land.tapped = False
        game.battlefield.add(land)
    
    # Tap one
    game.battlefield.cards[-1].tapped = True
    
    assert p1.available_mana(game) == 2, "Should have 2 untapped lands"


# ─── Alpha-Strike Detection Test ─────────────────────────────────────────

def test_alpha_strike_detection():
    """Agent should detect guaranteed lethal from evasive creatures."""
    agent = HeuristicAgent()
    
    # Create simple game state
    deck_cards = [Card("Mountain", "", "Land") for _ in range(40)]
    d1 = Deck()
    for c in deck_cards[:20]: d1.add_card(c)
    d2 = Deck()
    for c in deck_cards[20:]: d2.add_card(c)
    
    p1 = Player("Attacker", d1)
    p2 = Player("Defender", d2)
    p2.life = 5  # Low life
    
    game = Game([p1, p2])
    
    # Put a 5/5 flyer on the battlefield (guaranteed 5 damage to kill)
    flyer = Card("Dragon", "3RR", "Creature — Dragon")
    flyer.power = 5
    flyer.toughness = 5
    flyer.has_flying = True
    flyer.controller = p1
    flyer.summoning_sickness = False
    game.battlefield.add(flyer)
    
    # Call _choose_attackers
    candidates = [flyer]
    attackers = agent._choose_attackers(game, p1, candidates, p2)
    
    # Should include the flyer (lethal)
    assert flyer in attackers, "Agent should detect alpha strike with lethal flyer"


# ─── Run Tests ────────────────────────────────────────────────────────────

if __name__ == "__main__":
    tests = [
        test_classify_burn_as_aggro,
        test_classify_control,
        test_classify_empty_deck,
        test_classifier_returns_all_fields,
        test_threat_score_basic,
        test_threat_score_flying,
        test_threat_score_multi_keyword,
        test_available_mana,
        test_alpha_strike_detection,
    ]
    
    passed = 0
    failed = 0
    for test in tests:
        try:
            test()
            print(f"  ✅ {test.__name__}")
            passed += 1
        except Exception as e:
            print(f"  ❌ {test.__name__}: {e}")
            failed += 1
    
    print(f"\n{'='*50}")
    print(f"  {passed} passed, {failed} failed out of {len(tests)}")
    print(f"{'='*50}")
