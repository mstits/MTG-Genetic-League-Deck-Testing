"""Cross-Keyword Interaction Tests — edge cases from multi-keyword combinations.

Tests interactions between two or more keywords that share resolution
space (combat, targeting, damage prevention). These are the scenarios
most likely to produce engine bugs because they require multiple
subsystems to cooperate correctly.
"""

import pytest
from engine.card import Card
from engine.player import Player
from engine.deck import Deck
from engine.game import Game


def _creature(name, power, toughness, oracle_text="", **kw):
    return Card(name=name, cost="{1}", type_line="Creature — Test",
                oracle_text=oracle_text, base_power=power, base_toughness=toughness, **kw)


def _setup():
    deck = Deck()
    for i in range(60):
        deck.add_card(Card(name="Mountain", cost="", type_line="Basic Land — Mountain",
                           oracle_text="{T}: Add {R}.", produced_mana=['R']), 1)
    d2 = Deck()
    for i in range(60):
        d2.add_card(Card(name="Mountain", cost="", type_line="Basic Land — Mountain",
                         oracle_text="{T}: Add {R}.", produced_mana=['R']), 1)
    game = Game([Player("P1", deck), Player("P2", d2)])
    game.turn_count = 1
    game.game_over = False
    return game


def _place(game, card, idx=0):
    card.controller = game.players[idx]
    card.summoning_sickness = False
    card.tapped = False
    card.damage_taken = 0
    game.battlefield.add(card)
    return card


class TestKeywordInteractions:
    """Multi-keyword edge cases that stress-test the engine's conflict resolution."""

    def test_deathtouch_trample(self):
        """Deathtouch + Trample: 1 damage to each blocker (lethal), rest tramples.
        6/6 DT+Trample vs 2/2 blocker → 1 to blocker (lethal via DT), 5 tramples."""
        game = _setup()
        attacker = _place(game, _creature("Wurm", 6, 6, "Deathtouch\nTrample"))
        blocker = _place(game, _creature("Bear", 2, 2), 1)
        p2 = game.players[1]
        initial_life = p2.life

        game.combat_attackers = [attacker]
        game.combat_blockers = {attacker.id: [blocker]}
        game.resolve_combat_damage()

        # DT assigns 1 to blocker (lethal), tramples 5
        assert blocker not in game.battlefield.cards
        assert p2.life == initial_life - 5, \
            f"DT+Trample: expected {initial_life - 5} life, got {p2.life}"

    def test_first_strike_deathtouch(self):
        """First Strike + Deathtouch: kills blocker before it deals damage.
        1/1 FS+DT vs 5/5 → DT kills 5/5 in FS phase, attacker takes 0."""
        game = _setup()
        attacker = _place(game, _creature("Assassin", 1, 1, "First strike\nDeathtouch"))
        blocker = _place(game, _creature("Giant", 5, 5), 1)

        game.combat_attackers = [attacker]
        game.combat_blockers = {attacker.id: [blocker]}
        game.resolve_combat_damage()

        assert blocker not in game.battlefield.cards  # Killed by DT in FS phase
        assert attacker.damage_taken == 0  # Giant dead before normal damage

    def test_double_strike_lifelink(self):
        """Double Strike + Lifelink: gains life from BOTH damage phases.
        3/3 DS+LL unblocked → 3 life FS + 3 life normal = 6 total gained."""
        game = _setup()
        attacker = _place(game, _creature("Angel", 3, 3, "Double strike\nLifelink"))
        p1 = game.players[0]
        p2 = game.players[1]
        p1.life = 10

        game.combat_attackers = [attacker]
        game.combat_blockers = {}
        game.resolve_combat_damage()

        assert p1.life == 16  # 10 + 3 + 3 = 16
        assert p2.life == 14  # 20 - 3 - 3 = 14

    def test_protection_prevents_blocking(self):
        """Protection from red: can't be blocked by red creatures (702.16b)."""
        game = _setup()
        attacker = _place(game, _creature("Knight", 2, 2, "Protection from red"))
        red_blocker = _place(game, _creature("Goblin", 1, 1), 1)
        red_blocker.color_identity = ['R']

        # Protection prevents blocking by matching color
        assert attacker.is_protected_from(red_blocker)

    def test_indestructible_deathtouch(self):
        """Indestructible vs Deathtouch: indestructible wins (702.12b).
        Deathtouch marks the creature as 'deathtouch_damaged' but
        SBA doesn't destroy it due to indestructible."""
        game = _setup()
        indestr = _place(game, _creature("God", 4, 4, "Indestructible"))
        dt = _place(game, _creature("Viper", 1, 1, "Deathtouch"), 1)

        game.combat_attackers = [indestr]
        game.combat_blockers = {indestr.id: [dt]}
        game.resolve_combat_damage()

        # Indestructible survives deathtouch damage
        assert indestr in game.battlefield.cards
        assert dt not in game.battlefield.cards  # Viper dies to 4 damage

    def test_flying_reach_interaction(self):
        """Flying + Reach: Reach creature CAN block a flyer (702.17b).
        Conversely, a ground creature without reach CANNOT."""
        game = _setup()
        flyer = _place(game, _creature("Drake", 2, 2, "Flying"))
        reacher = _place(game, _creature("Spider", 1, 3, "Reach"), 1)
        ground = _place(game, _creature("Bear", 2, 2), 1)

        assert game._can_block(flyer, reacher) is True
        assert game._can_block(flyer, ground) is False

    def test_menace_deathtouch_blocker(self):
        """Menace: 2 blockers required, even if one has deathtouch.
        A single deathtouch creature is NOT sufficient to block menace."""
        game = _setup()
        menace = _place(game, _creature("Marauder", 3, 2, "Menace"))
        dt_blocker = _place(game, _creature("Viper", 1, 1, "Deathtouch"), 1)

        # Single blocker insufficient for menace
        assert not game._validate_blocking(menace, [dt_blocker])

    def test_lifelink_trample_blocked(self):
        """Lifelink + Trample: controller gains life from ALL damage dealt.
        5/5 LL+Trample blocked by 2/2 → 2 to blocker + 3 tramples = 5 life gained."""
        game = _setup()
        attacker = _place(game, _creature("Lifelord", 5, 5, "Lifelink\nTrample"))
        blocker = _place(game, _creature("Bear", 2, 2), 1)
        p1 = game.players[0]
        p1.life = 10

        game.combat_attackers = [attacker]
        game.combat_blockers = {attacker.id: [blocker]}
        game.resolve_combat_damage()

        assert p1.life == 15  # 10 + 2 (to blocker) + 3 (trample) = 15

    def test_double_strike_blocked(self):
        """Double Strike blocked: deals damage in both phases.
        4/4 DS blocked by 3/3 → FS: 4 damage kills blocker, normal: 4 to player."""
        game = _setup()
        attacker = _place(game, _creature("Warrior", 4, 4, "Double strike"))
        blocker = _place(game, _creature("Bear", 3, 3), 1)
        p2 = game.players[1]
        initial = p2.life

        game.combat_attackers = [attacker]
        game.combat_blockers = {attacker.id: [blocker]}
        game.resolve_combat_damage()

        # FS phase: 4 kills 3-toughness blocker. Blocker deals 0 (not FS).
        # Normal phase: attacker has DS so deals again. Blocker gone → goes to player? 
        # Actually: blocked creature stays blocked even if blocker dies.
        # In our engine, remaining damage from normal phase goes to blocker list (empty).
        # No trample → damage is "absorbed". So: blocker dies, no player damage.
        assert blocker not in game.battlefield.cards
        assert attacker.damage_taken == 0  # Blocker died in FS, never dealt damage
