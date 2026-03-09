"""Rule 613 Layering Matrix — stress-tests for P/T calculation conflicts.

Tests the engine's handling of the Layer 613 system for power/toughness:
  Layer 7a: Characteristic-defining abilities (e.g. Tarmogoyf)
  Layer 7b: Setting base P/T (e.g. "becomes a 0/1")
  Layer 7c: +1/+1 and -1/-1 counters
  Layer 7d: Static effects / continuous modifications (anthems, Equipment)
  Layer 7e: Temporary pump effects (e.g. Giant Growth until end of turn)

P/T formula: base_power + sum(temp_modifiers) + counters + attachments

If any test fails, it means the engine is miscalculating P/T for that
layering interaction, and the card ID is flagged.
"""

import pytest
from engine.card import Card
from engine.player import Player
from engine.deck import Deck
from engine.game import Game


def _make_creature(name, power, toughness, oracle_text=""):
    return Card(name=name, cost="{1}", type_line="Creature — Test",
                oracle_text=oracle_text, base_power=power, base_toughness=toughness)


def _setup():
    """Minimal game for testing."""
    deck = Deck()
    for i in range(60):
        deck.add_card(Card(name=f"Mountain", cost="", type_line="Basic Land — Mountain",
                           oracle_text="{T}: Add {R}.", produced_mana=['R']), 1)
    game = Game([Player("P1", deck), Player("P2", Deck())])
    # P2 needs cards too
    for i in range(60):
        game.players[1].library.add(Card(name=f"Mountain", cost="", type_line="Basic Land — Mountain",
                                          oracle_text="{T}: Add {R}.", produced_mana=['R']))
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


class TestLayeringMatrix:
    """Rule 613 stress-tests — each test documents the expected P/T
    calculation and flags if the engine disagrees."""

    def test_base_pt_only(self):
        """Layer 7a: Vanilla creature — P/T = base."""
        card = _make_creature("Vanilla Bear", 2, 2)
        assert card.power == 2
        assert card.toughness == 2

    def test_set_base_then_counter(self):
        """Layer 7b→7c: Set base P/T, then apply counters.
        A 3/3 with base set to 0/1 and 2x +1/+1 → 2/3.
        Engine: base_power=0,base_toughness=1, counters={'+1/+1':2}."""
        card = _make_creature("Morphed", 3, 3)
        # Layer 7b: Something sets base P/T to 0/1
        card.base_power = 0
        card.base_toughness = 1
        # Layer 7c: Two +1/+1 counters
        card.counters['+1/+1'] = 2
        assert card.power == 2, f"LAYERING FAIL [id={card.id}]: expected 2, got {card.power}"
        assert card.toughness == 3, f"LAYERING FAIL [id={card.id}]: expected 3, got {card.toughness}"

    def test_counter_then_anthem(self):
        """Layer 7c→7d: Counters + anthem stack additively.
        2/2 base + 1x +1/+1 counter + anthem +1/+1 → 4/4."""
        game = _setup()
        creature = _place(game, _make_creature("Bear", 2, 2))
        creature.counters['+1/+1'] = 1

        lord = _place(game, _make_creature("Lord", 2, 2))
        lord.static_effect = {'power': 1, 'toughness': 1, 'filter': 'other_creatures'}

        game._apply_static_effects()
        # 2 base + 1 counter + 1 anthem = 4
        assert creature.power == 4, f"LAYERING FAIL [id={creature.id}]: expected 4, got {creature.power}"
        assert creature.toughness == 4, f"LAYERING FAIL [id={creature.id}]: expected 4, got {creature.toughness}"

    def test_multiple_anthems_stack(self):
        """Layer 7d: Two anthems stack.
        1/1 + two different +1/+1 anthems → 3/3."""
        game = _setup()
        creature = _place(game, _make_creature("Token", 1, 1))

        lord1 = _place(game, _make_creature("Lord A", 2, 2))
        lord1.static_effect = {'power': 1, 'toughness': 1, 'filter': 'other_creatures'}

        lord2 = _place(game, _make_creature("Lord B", 2, 2))
        lord2.static_effect = {'power': 1, 'toughness': 1, 'filter': 'other_creatures'}

        game._apply_static_effects()
        assert creature.power == 3, f"LAYERING FAIL [id={creature.id}]: expected 3, got {creature.power}"
        assert creature.toughness == 3

    def test_anthem_plus_equipment(self):
        """Layer 7d: Anthem + Equipment are additive.
        2/2 + equipment +2/+0 + anthem +1/+1 → 5/3."""
        game = _setup()
        creature = _place(game, _make_creature("Bear", 2, 2))

        equip = Card(name="Sword", cost="{2}", type_line="Artifact — Equipment",
                     oracle_text="Equipped creature gets +2/+0.\nEquip {2}")
        _place(game, equip)
        equip.equipped_to = creature
        creature.attachments.append(equip)

        lord = _place(game, _make_creature("Lord", 2, 2))
        lord.static_effect = {'power': 1, 'toughness': 1, 'filter': 'other_creatures'}

        game._apply_static_effects()
        # 2 base + 2 equip + 1 anthem = 5 power
        # 2 base + 0 equip + 1 anthem = 3 toughness
        assert creature.power == 5, f"LAYERING FAIL [id={creature.id}]: expected 5, got {creature.power}"
        assert creature.toughness == 3, f"LAYERING FAIL [id={creature.id}]: expected 3, got {creature.toughness}"

    def test_counter_annihilation(self):
        """Layer 7c: +1/+1 and -1/-1 counters cancel.
        2/2 with 2x +1/+1 and 1x -1/-1 → net +1/+1 → 3/3."""
        card = _make_creature("Hydra", 2, 2)
        card.counters['+1/+1'] = 2
        card.counters['-1/-1'] = 1
        # Engine calculates: counter_bonus = +1/+1(2) - -1/-1(1) = +1
        assert card.power == 3, f"LAYERING FAIL [id={card.id}]: expected 3, got {card.power}"
        assert card.toughness == 3

    def test_temp_pump_with_counters(self):
        """Layer 7c+7e: Counters + temp pump are additive.
        2/2 + 1x +1/+1 counter + temp +2/+2 → 5/5 (until end of turn)."""
        card = _make_creature("Bear", 2, 2)
        card.counters['+1/+1'] = 1
        card._temp_modifiers.append({'power': 2, 'toughness': 2})
        assert card.power == 5, f"LAYERING FAIL [id={card.id}]: expected 5, got {card.power}"
        assert card.toughness == 5

    def test_tribal_anthem_selectivity(self):
        """Layer 7d: Tribal anthem only buffs matching creature types.
        Elf Lord: 'Other Elves +1/+1'. Non-Elf is unaffected."""
        game = _setup()
        lord = _place(game, _make_creature("Elf Lord", 2, 2))
        lord.creature_types = ['Elf']
        lord.static_effect = {'power': 1, 'toughness': 1, 'filter': 'type', 'type': 'Elf'}

        elf = _place(game, _make_creature("Llanowar Elf", 1, 1))
        elf.creature_types = ['Elf', 'Druid']

        human = _place(game, _make_creature("Soldier", 1, 1))
        human.creature_types = ['Human', 'Soldier']

        game._apply_static_effects()
        assert elf.power == 2, f"LAYERING FAIL [id={elf.id}]: Elf should be buffed to 2, got {elf.power}"
        assert human.power == 1, f"LAYERING FAIL [id={human.id}]: Human should be unbuffed at 1, got {human.power}"

    def test_anthem_removal_kills_zero_toughness(self):
        """Layer 7d removal: 0/0 token with anthem → remove anthem → 0/0 → dies.
        Tests that SBA correctly kills 0-toughness creatures."""
        game = _setup()
        # 0/0 base token with anthem making it 1/1
        token = _place(game, _make_creature("Spirit Token", 0, 0))
        token.type_line = "Creature — Spirit Token"  # is_token checks 'Token' in type_line

        lord = _place(game, _make_creature("Anthem Lord", 2, 2))
        lord.static_effect = {'power': 1, 'toughness': 1, 'filter': 'other_creatures'}

        game._apply_static_effects()
        assert token.power == 1  # Alive due to anthem
        assert token.toughness == 1

        # Anthem lord removed
        game.battlefield.remove(lord)
        game._apply_static_effects()  # Recalculate
        assert token.toughness == 0
        game.check_state_based_actions()
        assert token not in game.battlefield.cards, \
            f"LAYERING FAIL [id={token.id}]: 0/0 token should die to SBA 704.5f"

    def test_set_base_then_anthem_then_counter(self):
        """Full layer chain: 7b → 7c → 7d.
        Original 5/5, set base to 1/1, +1/+1 counter, +1/+1 anthem → 3/3."""
        game = _setup()
        card = _place(game, _make_creature("Morphed Big", 5, 5))

        # Layer 7b: set base to 1/1
        card.base_power = 1
        card.base_toughness = 1

        # Layer 7c: +1/+1 counter
        card.counters['+1/+1'] = 1

        # Layer 7d: anthem
        lord = _place(game, _make_creature("Lord", 2, 2))
        lord.static_effect = {'power': 1, 'toughness': 1, 'filter': 'other_creatures'}
        game._apply_static_effects()

        # 1 base + 1 counter + 1 anthem = 3
        assert card.power == 3, f"LAYERING FAIL [id={card.id}]: expected 3, got {card.power}"
        assert card.toughness == 3, f"LAYERING FAIL [id={card.id}]: expected 3, got {card.toughness}"

    def test_equipment_plus_counter_plus_temp(self):
        """All P/T modification layers combined.
        1/1 + equip +2/+0 + counter +1/+1 + temp +1/+1 → 5/3."""
        card = _make_creature("Tiny", 1, 1)
        equip = Card(name="Axe", cost="{1}", type_line="Artifact — Equipment",
                     oracle_text="Equipped creature gets +2/+0.\nEquip {1}")
        card.attachments.append(equip)
        card.counters['+1/+1'] = 1
        card._temp_modifiers.append({'power': 1, 'toughness': 1})
        # 1 base + 2 equip + 1 counter + 1 temp = 5 power
        # 1 base + 0 equip + 1 counter + 1 temp = 3 toughness
        assert card.power == 5, f"LAYERING FAIL [id={card.id}]: expected 5, got {card.power}"
        assert card.toughness == 3, f"LAYERING FAIL [id={card.id}]: expected 3, got {card.toughness}"
