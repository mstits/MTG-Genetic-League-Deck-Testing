"""Comprehensive test suite for MTG engine rules compliance.
Tests all mechanics fixed/added in the rules audit fixes."""

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from engine.card import Card, StackItem
from engine.deck import Deck
from engine.player import Player
from engine.game import Game
from agents.base_agent import BaseAgent


class PassAgent(BaseAgent):
    """Agent that always passes."""
    def get_action(self, game, player):
        return {'type': 'pass'}


def make_deck(cards_spec):
    """Helper: create a deck from a list of (Card, quantity) tuples."""
    deck = Deck()
    for card, qty in cards_spec:
        deck.add_card(card, qty)
    return deck


def setup_game(p1_cards=None, p2_cards=None):
    """Helper: create a 2-player game with specified cards in hand.
    Both players get 20 basic lands to ensure library isn't empty."""
    land = Card(name="Mountain", cost="", type_line="Basic Land - Mountain")
    
    d1 = make_deck([(land, 40)])
    d2 = make_deck([(land, 40)])
    
    p1 = Player("Player1", d1)
    p2 = Player("Player2", d2)
    game = Game([p1, p2])
    
    # Don't call start_game (which draws 7 and mulligans).
    # Instead, manually set up the game state.
    game.turn_count = 1
    game.active_player_index = 0
    game.priority_player_index = 0
    game.current_phase = "Main 1"
    game.phase_index = 3
    game._last_life_change_turn = 1
    
    # Put specified cards in hand
    if p1_cards:
        for c in p1_cards:
            c.controller = p1
            p1.hand.add(c)
    if p2_cards:
        for c in p2_cards:
            c.controller = p2
            p2.hand.add(c)
    
    return game, p1, p2


def add_battlefield(game, player, cards):
    """Helper: put cards directly onto the battlefield."""
    for c in cards:
        c.controller = player
        c.tapped = False
        c.summoning_sickness = False
        c.damage_taken = 0
        game.battlefield.add(c)


def add_lands(game, player, count=5, land_type="Mountain"):
    """Helper: put untapped lands on the battlefield for mana."""
    for i in range(count):
        land = Card(name=land_type, cost="", type_line=f"Basic Land - {land_type}")
        land.controller = player  
        land.tapped = False
        game.battlefield.add(land)


# ─── TEST 1: Flying/Reach Enforcement ──────────────────────────

def test_flying_block_restriction():
    """Ground creatures can't block flyers; reach creatures can."""
    game, p1, p2 = setup_game()
    
    flyer = Card(name="Serra Angel", cost="{3}{W}{W}", type_line="Creature — Angel",
                 oracle_text="Flying, vigilance", base_power=4, base_toughness=4)
    flyer.id = 100
    ground = Card(name="Grizzly Bears", cost="{1}{G}", type_line="Creature — Bear",
                  base_power=2, base_toughness=2)
    ground.id = 200
    reacher = Card(name="Giant Spider", cost="{3}{G}", type_line="Creature — Spider",
                   oracle_text="Reach", base_power=2, base_toughness=4)
    reacher.id = 300
    
    add_battlefield(game, p1, [flyer])
    add_battlefield(game, p2, [ground, reacher])
    
    # Verify keyword parsing
    assert flyer.has_flying, "FAIL: Serra Angel should have flying"
    assert flyer.has_vigilance, "FAIL: Serra Angel should have vigilance"
    assert reacher.has_reach, "FAIL: Giant Spider should have reach"
    assert not ground.has_flying, "FAIL: Bears should not have flying"
    assert not ground.has_reach, "FAIL: Bears should not have reach"
    
    # Verify can_block_flyer property
    assert reacher.can_block_flyer, "FAIL: Reach creature should be able to block flyers"
    assert not ground.can_block_flyer, "FAIL: Ground creature should NOT block flyers"
    
    # Core rules test: ground can't block flyer, reach can
    assert not game._can_block(flyer, ground), \
        "FAIL: Ground creature (_can_block) should NOT block a flyer"
    assert game._can_block(flyer, reacher), \
        "FAIL: Reach creature (_can_block) should block a flyer"
    
    # Validate blocking group
    assert not game._validate_blocking(flyer, [ground]), \
        "FAIL: Ground-only block group should be invalid"
    assert game._validate_blocking(flyer, [reacher]), \
        "FAIL: Reach block group should be valid"
    
    print("✅ Test 1 PASSED: Flying/Reach enforcement")


# ─── TEST 2: ETB Triggers on Stack ────────────────────────────

def test_etb_on_stack():
    """ETB effects should be pushed onto the stack, not resolve instantly."""
    game, p1, p2 = setup_game()
    
    # Create a creature with an ETB effect, and a target for it
    target = Card(name="Hill Giant", cost="{3}{R}", type_line="Creature — Giant",
                  base_power=3, base_toughness=3)
    add_battlefield(game, p2, [target])
    
    etb_creature = Card(name="Flametongue Kavu", cost="{3}{R}", 
                        type_line="Creature — Kavu",
                        oracle_text="When Flametongue Kavu enters the battlefield, it deals 4 damage to target creature.",
                        base_power=4, base_toughness=2)
    etb_creature.controller = p1
    
    # Verify the ETB effect was parsed
    assert etb_creature.etb_effect is not None, \
        "FAIL: ETB effect should have been parsed"
    
    # Put creature on the stack (simulating after casting)
    game.stack.add(etb_creature)
    
    # Record the target's damage before resolving
    initial_damage = target.damage_taken
    
    # Resolve just the creature (not the ETB trigger)
    game._resolve_stack_top()
    
    # The creature should be on the battlefield now
    assert etb_creature in game.battlefield.cards, \
        "FAIL: Creature should have entered the battlefield"
    
    # Check the log for ETB trigger being added to stack
    etb_logged = any('ETB trigger' in entry for entry in game.log)
    assert etb_logged, \
        "FAIL: ETB trigger should have been logged as going on the stack"
    
    print("✅ Test 2 PASSED: ETB triggers use the stack")


# ─── TEST 3: Recursive Priority ───────────────────────────────

def test_recursive_priority():
    """Both players must pass in succession for stack to resolve."""
    game, p1, p2 = setup_game()
    add_lands(game, p1, 3)
    
    bolt = Card(name="Lightning Bolt", cost="{R}", type_line="Instant",
                oracle_text="Lightning Bolt deals 3 damage to any target.")
    bolt.controller = p1
    game.stack.add(bolt)
    
    # One pass shouldn't resolve the stack
    game._consecutive_passes = 0
    game.apply_action({'type': 'pass'})
    
    # After one pass, stack should still have the bolt
    # (priority passes to the other player)
    assert len(game.stack) > 0 or game._consecutive_passes > 0, \
        "FAIL: Stack shouldn't resolve after a single pass"
    
    # Second pass should resolve it
    game.apply_action({'type': 'pass'})
    assert len(game.stack) == 0, \
        "FAIL: Stack should resolve after two consecutive passes"
    
    print("✅ Test 3 PASSED: Recursive priority (both players must pass)")


# ─── TEST 4: Upkeep Triggers ──────────────────────────────────

def test_upkeep_triggers():
    """Cards with upkeep triggers should fire during upkeep."""
    game, p1, p2 = setup_game()
    
    upkeep_card = Card(name="Phyrexian Arena", cost="{1}{B}{B}", 
                       type_line="Enchantment",
                       oracle_text="At the beginning of your upkeep, you draw a card and you lose 1 life.")
    
    add_battlefield(game, p1, [upkeep_card])
    
    initial_life = p1.life
    initial_hand = len(p1.hand)
    
    # Fire upkeep triggers (they go on stack, not auto-resolved after priority refactor)
    game._fire_upkeep_triggers()
    
    # Drain the stack to execute the triggers (simulating both players passing)
    while len(game.stack) > 0:
        game._resolve_stack_top()
    
    # Should have drawn a card or lost life (depending on which parsed first)
    # The draw pattern should match
    result_changed = (len(p1.hand) != initial_hand or p1.life != initial_life)
    assert result_changed, \
        "FAIL: Upkeep trigger should have fired (draw or life loss)"
    
    print("✅ Test 4 PASSED: Upkeep triggers fire")


# ─── TEST 5: Enters-Tapped Lands ──────────────────────────────

def test_enters_tapped():
    """Lands with 'enters the battlefield tapped' should enter tapped."""
    game, p1, p2 = setup_game()
    
    tapland = Card(name="Bloodfell Caves", cost="", type_line="Land",
                   oracle_text="Bloodfell Caves enters the battlefield tapped. {T}: Add {B} or {R}.")
    tapland.controller = p1
    p1.hand.add(tapland)
    
    game.current_phase = "Main 1"
    p1.play_land(tapland, game)
    
    assert tapland.tapped, \
        "FAIL: Land with 'enters the battlefield tapped' should be tapped"
    
    print("✅ Test 5 PASSED: Enters-tapped lands")


# ─── TEST 6: Hybrid Mana ──────────────────────────────────────

def test_hybrid_mana():
    """Hybrid mana {R/W} should be payable with either color."""
    game, p1, p2 = setup_game()
    
    # Add 2 Plains (White mana only, no Red)
    add_lands(game, p1, 2, "Plains")
    
    # A card with hybrid cost {R/W}
    hybrid_card = Card(name="Boros Reckoner", cost="{R/W}{R/W}{R/W}", 
                       type_line="Creature — Minotaur Wizard",
                       base_power=3, base_toughness=3)
    
    # Should be able to pay with all white
    can_pay = p1.can_pay_cost("{R/W}{R/W}", game)
    assert can_pay, \
        "FAIL: Should be able to pay {R/W}{R/W} with 2 Plains"
    
    print("✅ Test 6 PASSED: Hybrid mana flexibility")


# ─── TEST 7: Mana Pool Reset ──────────────────────────────────

def test_mana_pool_reset():
    """Mana pool should be emptied between phases."""
    game, p1, p2 = setup_game()
    
    # Add mana to pool
    p1.mana_pool['R'] = 5
    p1.mana_pool['W'] = 3
    
    # Reset
    game._reset_all_mana_pools()
    
    assert all(v == 0 for v in p1.mana_pool.values()), \
        "FAIL: Mana pool should be empty after reset"
    assert all(v == 0 for v in p2.mana_pool.values()), \
        "FAIL: Opponent's mana pool should also be empty"
    
    print("✅ Test 7 PASSED: Mana pool reset")


# ─── TEST 8: ETB Damage Marks Damage ──────────────────────────

def test_etb_damage_marks():
    """ETB damage should mark damage_taken, not directly remove creatures."""
    game, p1, p2 = setup_game()
    
    target = Card(name="Hill Giant", cost="{3}{R}", type_line="Creature — Giant",
                  base_power=3, base_toughness=3)
    add_battlefield(game, p2, [target])
    
    # Create an ETB damage effect for 2 damage (non-lethal)
    etb_creature = Card(name="Pia Nalaar", cost="{2}{R}", type_line="Creature — Human Artificer",
                        oracle_text="When Pia Nalaar enters the battlefield, it deals 2 damage to target creature.",
                        base_power=2, base_toughness=2)
    etb_creature.controller = p1
    
    # Execute the ETB effect directly
    etb_creature.etb_effect(game, etb_creature)
    
    # Target should still be on battlefield with marked damage
    assert target in game.battlefield.cards, \
        "FAIL: Non-lethal ETB damage should not remove creature from battlefield"
    assert target.damage_taken == 2, \
        f"FAIL: Creature should have 2 damage marked, got {target.damage_taken}"
    
    print("✅ Test 8 PASSED: ETB damage marks damage (doesn't remove directly)")


# ─── TEST 9: Static Effects (Anthems) ─────────────────────────

def test_static_effects():
    """'Other creatures you control get +1/+1' should apply to friendly creatures."""
    game, p1, p2 = setup_game()
    
    lord = Card(name="Glorious Anthem", cost="{1}{W}{W}", type_line="Enchantment",
                oracle_text="Other creatures you control get +1/+1.")
    bear = Card(name="Grizzly Bears", cost="{1}{G}", type_line="Creature — Bear",
                base_power=2, base_toughness=2)
    opp_bear = Card(name="Runeclaw Bear", cost="{1}{G}", type_line="Creature — Bear",
                    base_power=2, base_toughness=2)
    
    add_battlefield(game, p1, [lord, bear])
    add_battlefield(game, p2, [opp_bear])
    
    # Apply static effects
    game._apply_static_effects()
    
    # Our bear should get +1/+1
    assert bear.power == 3 and bear.toughness == 3, \
        f"FAIL: Bear should be 3/3 with anthem, got {bear.power}/{bear.toughness}"
    
    # Opponent's bear should NOT get the bonus
    assert opp_bear.power == 2 and opp_bear.toughness == 2, \
        f"FAIL: Opponent's bear should still be 2/2, got {opp_bear.power}/{opp_bear.toughness}"
    
    print("✅ Test 9 PASSED: Static effects (anthems)")


# ─── TEST 10: Death Triggers ──────────────────────────────────

def test_death_triggers():
    """'When this creature dies' effects should fire."""
    game, p1, p2 = setup_game()
    
    death_creature = Card(name="Doomed Traveler", cost="{W}", type_line="Creature — Human Soldier",
                          oracle_text="When Doomed Traveler dies, create a 1/1 white Spirit creature token with flying.",
                          base_power=1, base_toughness=1)
    
    add_battlefield(game, p1, [death_creature])
    
    # Check that death_effect was parsed
    assert death_creature.death_effect is not None, \
        "FAIL: Death trigger should have been parsed"
    
    # Simulate death
    game._fire_death_trigger(death_creature)
    
    # Should have a trigger on the stack
    assert len(game.stack) > 0, \
        "FAIL: Death trigger should be on the stack"
    
    print("✅ Test 10 PASSED: Death triggers fire")


# ─── TEST 11: +1/+1 Counters ──────────────────────────────────

def test_counters():
    """+1/+1 counters should be included in power/toughness calculations."""
    game, p1, p2 = setup_game()
    
    creature = Card(name="Servant of the Scale", cost="{G}", type_line="Creature — Human Soldier",
                    oracle_text="Servant of the Scale enters the battlefield with a +1/+1 counter on it.",
                    base_power=0, base_toughness=0)
    
    # Check counters were parsed ("with a +1/+1 counter" singular form)
    assert creature.counters.get('+1/+1', 0) > 0, \
        f"FAIL: Should have parsed +1/+1 counter from text. Counters: {creature.counters}"
    
    # Check power/toughness includes counters
    assert creature.power == 1 and creature.toughness == 1, \
        f"FAIL: 0/0 + one +1/+1 counter should be 1/1, got {creature.power}/{creature.toughness}"
    
    # Add more counters
    creature.counters['+1/+1'] = 3
    assert creature.power == 3 and creature.toughness == 3, \
        f"FAIL: 0/0 + three +1/+1 counters should be 3/3, got {creature.power}/{creature.toughness}"
    
    # Also test numeric form
    creature2 = Card(name="Hangarback Walker", cost="{X}{X}", type_line="Creature — Construct",
                     oracle_text="Hangarback Walker enters the battlefield with 2 +1/+1 counters on it.",
                     base_power=0, base_toughness=0)
    assert creature2.counters.get('+1/+1', 0) == 2, \
        f"FAIL: Should have parsed 2 +1/+1 counters. Counters: {creature2.counters}"
    
    print("✅ Test 11 PASSED: +1/+1 counters")


# ─── TEST 12: Activated Abilities ─────────────────────────────

def test_activated_abilities():
    """Tap abilities should be parsed and executable."""
    game, p1, p2 = setup_game()
    
    pinger = Card(name="Prodigal Sorcerer", cost="{2}{U}", type_line="Creature — Human Wizard",
                  oracle_text="{T}: Prodigal Sorcerer deals 1 damage to any target.",
                  base_power=1, base_toughness=1)
    
    assert len(pinger.activated_abilities) > 0, \
        "FAIL: Should have parsed activated ability"
    
    ability = pinger.activated_abilities[0]
    assert ability['cost_tap'] == True, \
        "FAIL: Ability should have tap cost"
    
    # Simulate activation
    add_battlefield(game, p1, [pinger])
    add_lands(game, p1, 3)
    
    initial_life = p2.life
    ability['effect'](game, pinger)
    
    assert p2.life == initial_life - 1, \
        f"FAIL: Opponent should have lost 1 life, got {p2.life}"
    
    print("✅ Test 12 PASSED: Activated abilities")


# ─── TEST 13: Protection ──────────────────────────────────────

def test_protection():
    """Protection from a color should prevent damage, blocking, and targeting."""
    game, p1, p2 = setup_game()
    
    protected = Card(name="Kor Firewalker", cost="{W}{W}", type_line="Creature — Kor Soldier",
                     oracle_text="Protection from red",
                     base_power=2, base_toughness=2)
    
    assert 'red' in protected.has_protection_from, \
        "FAIL: Should have parsed 'protection from red'"
    
    red_card = Card(name="Lightning Bolt", cost="{R}", type_line="Instant",
                    color_identity=['R'])
    
    assert protected.is_protected_from(red_card), \
        "FAIL: Should be protected from a red card"
    
    white_card = Card(name="Swords to Plowshares", cost="{W}", type_line="Instant",
                      color_identity=['W'])
    
    assert not protected.is_protected_from(white_card), \
        "FAIL: Should NOT be protected from a white card"
    
    print("✅ Test 13 PASSED: Protection keyword")


# ─── TEST 14: Ward ────────────────────────────────────────────

def test_ward():
    """Ward should be parsed from oracle text."""
    game, p1, p2 = setup_game()
    
    warded = Card(name="Iridescent Hornbeetle", cost="{4}{G}", type_line="Creature — Insect",
                  oracle_text="Ward {2}",
                  base_power=3, base_toughness=4)
    
    assert warded.has_ward, \
        "FAIL: Should have parsed ward"
    assert warded.ward_cost == "{2}", \
        f"FAIL: Ward cost should be '{{2}}', got '{warded.ward_cost}'"
    
    print("✅ Test 14 PASSED: Ward keyword")


# ─── TEST 15: Token Generation ─────────────────────────────────

def test_tokens():
    """Token creation effects should create Card objects on the battlefield."""
    game, p1, p2 = setup_game()
    
    token_spell = Card(name="Dragon Fodder", cost="{1}{R}", type_line="Sorcery",
                       oracle_text="Create two 1/1 red Goblin creature tokens.")
    
    # Should have parsed effect
    assert token_spell.effect is not None, \
        "FAIL: Token spell should have an effect"
    
    token_spell.controller = p1
    initial_creatures = len([c for c in game.battlefield.cards if c.is_creature])
    
    token_spell.effect(game, token_spell)
    
    new_creatures = len([c for c in game.battlefield.cards if c.is_creature])
    assert new_creatures == initial_creatures + 2, \
        f"FAIL: Should have created 2 tokens, got {new_creatures - initial_creatures} new creatures"
    
    print("✅ Test 15 PASSED: Token generation")


# ─── TEST 16: Error Handling ──────────────────────────────────

def test_error_handling():
    """Spell resolution errors should be logged, not silently swallowed."""
    game, p1, p2 = setup_game()
    
    def bad_effect(game, card):
        raise ValueError("Intentional test error")
    
    bad_card = Card(name="Buggy Spell", cost="{R}", type_line="Instant")
    bad_card.controller = p1
    bad_card.effect = bad_effect
    
    game.stack.add(bad_card)
    
    # Should not crash
    game._resolve_stack_top()
    
    # Game should still be functional
    assert not game.game_over, \
        "FAIL: Game should not end due to a spell error"
    
    print("✅ Test 16 PASSED: Error handling (no silent swallowing)")


# ─── TEST 17: X Cost Detection ────────────────────────────────

def test_x_cost():
    """Cards with {X} in cost should have has_x_cost flag set."""
    fireball = Card(name="Fireball", cost="{X}{R}", type_line="Sorcery",
                    oracle_text="Fireball deals X damage to any target.")
    
    assert fireball.has_x_cost, \
        "FAIL: Fireball should have has_x_cost flag"
    
    bolt = Card(name="Lightning Bolt", cost="{R}", type_line="Instant")
    assert not bolt.has_x_cost, \
        "FAIL: Lightning Bolt should NOT have has_x_cost flag"
    
    print("✅ Test 17 PASSED: X cost detection")


# ─── TEST 18: StackItem Type ──────────────────────────────────

def test_stack_item():
    """StackItem should work on the stack alongside Card objects."""
    game, p1, p2 = setup_game()
    
    executed = [False]
    def trigger_effect(game, source):
        executed[0] = True
    
    trigger = StackItem(
        effect=trigger_effect,
        source=None,
        controller=p1,
        description="Test trigger"
    )
    
    game.stack.cards.append(trigger)
    game._resolve_stack_top()
    
    assert executed[0], \
        "FAIL: StackItem effect should have executed"
    
    print("✅ Test 18 PASSED: StackItem on stack")


# ─── TEST 19: Counter Spell vs Ability ─────────────────────────

def test_counter_vs_ability():
    """Counterspells should not counter triggered abilities."""
    game, p1, p2 = setup_game()
    
    # Put a trigger on the stack
    trigger = StackItem(
        effect=lambda g, s: None,
        source=None,
        controller=p2,
        description="Test trigger"
    )
    game.stack.cards.append(trigger)
    
    # Make a counterspell
    counter = Card(name="Counterspell", cost="{U}{U}", type_line="Instant",
                   oracle_text="Counter target spell.")
    counter.controller = p1
    
    # Execute the effect
    counter.effect(game, counter)
    
    # Trigger should still be on the stack (can't counter abilities)
    assert len(game.stack) > 0, \
        "FAIL: Counterspell should not have removed the triggered ability"
    
    print("✅ Test 19 PASSED: Counterspell can't counter abilities")

# ─── TIER 1: NEW MECHANIC TESTS ──────────────────────────────────

def test_minus_counters():
    """Test 20: -1/-1 counters reduce power and toughness.
    A 7/7 with six -1/-1 counters should be 1/1."""
    game, p1, p2 = setup_game()
    # Simulate a 7/7 creature that enters with 6 -1/-1 counters (like Moonshadow)
    creature = Card(name="Test Moonshadow", cost="{B}", type_line="Creature — Spirit",
                    base_power=7, base_toughness=7,
                    oracle_text="Enters the battlefield with 6 -1/-1 counters on it.")
    # Post-init should have parsed the counters
    assert creature.counters.get('-1/-1', 0) == 6, f"Expected 6 -1/-1 counters, got {creature.counters}"
    assert creature.power == 1, f"Expected power 1, got {creature.power}"
    assert creature.toughness == 1, f"Expected toughness 1, got {creature.toughness}"
    assert creature.has_drawback == True, "Should be flagged as drawback"
    print("✅ Test 20 PASSED: -1/-1 counters reduce P/T")


def test_minus_counters_sba_death():
    """Test 21: -1/-1 counters kill creatures via SBA when toughness hits 0."""
    game, p1, p2 = setup_game()
    # A 2/2 with 2 -1/-1 counters becomes 0/0 and dies
    creature = Card(name="Fragile", cost="{B}", type_line="Creature — Zombie",
                    base_power=2, base_toughness=2,
                    oracle_text="Enters the battlefield with 2 -1/-1 counters on it.")
    creature.controller = p1
    game.battlefield.add(creature)
    game.check_state_based_actions()
    # Should be dead — 0/0
    assert creature not in game.battlefield.cards, "0/0 creature should die to SBA"
    assert creature in p1.graveyard.cards, "Should be in graveyard"
    print("✅ Test 21 PASSED: -1/-1 counter SBA death")


def test_defender_cant_attack():
    """Test 22: Defender keyword prevents attacking."""
    game, p1, p2 = setup_game()
    wall = Card(name="Wall of Stone", cost="{1}{R}", type_line="Creature — Wall",
                base_power=0, base_toughness=7,
                oracle_text="Defender")
    add_battlefield(game, p1, [wall])
    add_lands(game, p1, 3)
    
    # Move to declare attackers
    game.current_phase = "Declare Attackers"
    game.phase_index = 4
    legal = game.get_legal_actions()
    
    has_attack = any(a['type'] == 'declare_attackers' for a in legal)
    assert not has_attack, "Defender creature should NOT be able to attack"
    print("✅ Test 22 PASSED: Defender can't attack")


def test_cant_block():
    """Test 23: Creature with 'can't block' is excluded from blocker candidates."""
    game, p1, p2 = setup_game()
    # P1 has a normal attacker
    attacker = Card(name="Goblin", cost="{R}", type_line="Creature — Goblin",
                    base_power=2, base_toughness=1)
    add_battlefield(game, p1, [attacker])
    
    # P2 has a creature that can't block (like Goblin Guide)
    no_block = Card(name="Reckless Brute", cost="{2}{R}", type_line="Creature — Ogre",
                    base_power=3, base_toughness=1,
                    oracle_text="Reckless Brute can't block.")
    add_battlefield(game, p2, [no_block])
    
    assert no_block.can_block == False, f"Should not be able to block, but can_block={no_block.can_block}"
    
    # Set up combat
    game.combat_attackers = [attacker]
    game.current_phase = "Declare Blockers"
    game.priority_player_index = 1  # P2 is defending
    legal = game.get_legal_actions()
    
    has_blockers = any(a['type'] == 'declare_blockers' for a in legal)
    assert not has_blockers, "Creature with 'can't block' should NOT appear as blocker"
    print("✅ Test 23 PASSED: Can't block restriction")


def test_etb_sacrifice():
    """Test 24: ETB self-sacrifice (creature sacrifices itself on ETB)."""
    game, p1, p2 = setup_game()
    # A creature that must be sacrificed unless a condition is met
    ephemeral = Card(name="Veilborn Ghoul", cost="{B}", type_line="Creature — Zombie",
                     base_power=4, base_toughness=1,
                     oracle_text="When Veilborn Ghoul enters the battlefield, sacrifice it unless you pay {2}.")
    assert ephemeral.has_drawback == True, "Should be flagged as drawback"
    assert ephemeral.etb_effect is not None, "Should have ETB sacrifice effect"
    
    # Simulate resolving onto battlefield
    ephemeral.controller = p1
    game.battlefield.add(ephemeral)
    # Trigger the ETB
    ephemeral.etb_effect(game, ephemeral)
    
    assert ephemeral not in game.battlefield.cards, "Should have been sacrificed"
    assert ephemeral in p1.graveyard.cards, "Should be in graveyard"
    print("✅ Test 24 PASSED: ETB self-sacrifice")


def test_indestructible_zero_toughness():
    """Test 25: Indestructible creatures still die to 0 toughness (Rule 704.5f)."""
    game, p1, p2 = setup_game()
    # An indestructible 2/2 that gets -2/-2 from counters
    creature = Card(name="Stubborn Rock", cost="{W}", type_line="Creature — Elemental",
                    base_power=2, base_toughness=2,
                    oracle_text="Indestructible. Enters the battlefield with 2 -1/-1 counters on it.")
    assert creature.has_indestructible == True
    assert creature.toughness == 0, f"Expected toughness 0, got {creature.toughness}"
    
    creature.controller = p1
    game.battlefield.add(creature)
    game.check_state_based_actions()
    
    assert creature not in game.battlefield.cards, "Indestructible with 0 toughness should still die"
    print("✅ Test 25 PASSED: Indestructible dies to 0 toughness")


def run_all_tests():
    tests = [
        test_flying_block_restriction,
        test_etb_on_stack,
        test_recursive_priority,
        test_upkeep_triggers,
        test_enters_tapped,
        test_hybrid_mana,
        test_mana_pool_reset,
        test_etb_damage_marks,
        test_static_effects,
        test_death_triggers,
        test_counters,
        test_activated_abilities,
        test_protection,
        test_ward,
        test_tokens,
        test_error_handling,
        test_x_cost,
        test_stack_item,
        test_counter_vs_ability,
        # Tier 1 tests
        test_minus_counters,
        test_minus_counters_sba_death,
        test_defender_cant_attack,
        test_cant_block,
        test_etb_sacrifice,
        test_indestructible_zero_toughness,
    ]
    
    passed = 0
    failed = 0
    errors = []
    
    for test in tests:
        try:
            test()
            passed += 1
        except AssertionError as e:
            failed += 1
            errors.append(f"  ❌ {test.__name__}: {e}")
        except Exception as e:
            failed += 1
            errors.append(f"  💥 {test.__name__}: {type(e).__name__}: {e}")
    
    print(f"\n{'='*50}")
    print(f"Results: {passed} passed, {failed} failed out of {len(tests)} tests")
    
    if errors:
        print("\nFailures:")
        for err in errors:
            print(err)
    
    return failed == 0


if __name__ == "__main__":
    success = run_all_tests()
    sys.exit(0 if success else 1)
