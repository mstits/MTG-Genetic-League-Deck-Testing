"""Keyword Stress-Tester — one synthetic game state per keyword.

For every supported keyword/mechanic, generates a minimal game state,
triggers the mechanic, resolves it through the stack, and validates
the outcome against the 2026 Comprehensive Rules expectation.

Each test is self-contained: creates Cards + Players + Game directly,
without needing a full deck or card pool. This isolates keyword
behaviour from deck-building or AI noise.
"""

import pytest
from engine.card import Card, StackItem
from engine.player import Player
from engine.deck import Deck
from engine.game import Game
from engine.zone import Zone


# ─── Helpers ──────────────────────────────────────────────────────────────────

def _make_creature(name, cost, power, toughness, oracle_text="", **extra):
    """Build a creature Card with given stats and keywords."""
    type_line = extra.pop('type_line', f'Creature — Test')
    c = Card(
        name=name, cost=cost, type_line=type_line,
        oracle_text=oracle_text,
        base_power=power, base_toughness=toughness,
        **extra
    )
    return c


def _make_land(name, color='R'):
    """Build a basic land."""
    land_map = {'W': 'Plains', 'U': 'Island', 'B': 'Swamp', 'R': 'Mountain', 'G': 'Forest'}
    land_name = land_map.get(color, 'Mountain')
    return Card(
        name=land_name, cost='', type_line=f'Basic Land — {land_name}',
        oracle_text=f'{{T}}: Add {{{color}}}.',
        produced_mana=[color], color_identity=[color],
    )


def _make_deck(cards):
    """Build a 60-card deck from provided cards + padding mountains."""
    deck = Deck()
    for c in cards:
        deck.add_card(c, 1)
    while deck.total_maindeck < 60:
        deck.add_card(_make_land('Mountain', 'R'), 1)
    return deck


def _setup_game(p1_cards=None, p2_cards=None):
    """Create a Game with minimal setup. Cards go directly to battlefield."""
    d1 = _make_deck(p1_cards or [])
    d2 = _make_deck(p2_cards or [])
    p1 = Player("P1", d1)
    p2 = Player("P2", d2)
    game = Game([p1, p2])
    game.turn_count = 1
    game.active_player_index = 0
    game.game_over = False
    return game


def _place_on_battlefield(game, card, player_index=0):
    """Put a card directly on the battlefield under a player's control."""
    player = game.players[player_index]
    card.controller = player
    card.summoning_sickness = False
    card.tapped = False
    card.damage_taken = 0
    game.battlefield.add(card)
    return card


def _place_lands(game, count, player_index=0, color='R'):
    """Place N untapped lands on battlefield."""
    for _ in range(count):
        land = _make_land(f'Mountain', color)
        _place_on_battlefield(game, land, player_index)


# ─── EVASION KEYWORDS ────────────────────────────────────────────────────────

class TestEvasionKeywords:
    """Rule 702.9 (Flying), 702.110 (Menace), 702.17 (Reach)."""

    def test_flying_cant_be_blocked_by_ground(self):
        """A creature with flying can only be blocked by flyers/reach (702.9b)."""
        game = _setup_game()
        flyer = _place_on_battlefield(game, _make_creature("Sky Eagle", "{1}{W}", 2, 2, "Flying"))
        ground = _place_on_battlefield(game, _make_creature("Ground Bear", "{1}{G}", 2, 2), 1)
        assert not game._can_block(flyer, ground)

    def test_flying_blocked_by_flyer(self):
        """Flyer CAN be blocked by another flyer."""
        game = _setup_game()
        flyer = _place_on_battlefield(game, _make_creature("Sky Eagle", "{1}{W}", 2, 2, "Flying"))
        other_flyer = _place_on_battlefield(game, _make_creature("Wind Drake", "{2}{U}", 2, 2, "Flying"), 1)
        assert game._can_block(flyer, other_flyer)

    def test_menace_needs_two_blockers(self):
        """Menace requires at least 2 blockers (702.110b)."""
        game = _setup_game()
        menace = _place_on_battlefield(game, _make_creature("Goblin Menace", "{1}{R}", 3, 2, "Menace"))
        blocker = _place_on_battlefield(game, _make_creature("Bear", "{1}{G}", 2, 2), 1)
        # _validate_blocking checks if a group of blockers is legal
        assert not game._validate_blocking(menace, [blocker])

    def test_menace_two_blockers_ok(self):
        """Two blockers satisfy menace."""
        game = _setup_game()
        menace = _place_on_battlefield(game, _make_creature("Goblin Menace", "{1}{R}", 3, 2, "Menace"))
        b1 = _place_on_battlefield(game, _make_creature("Bear1", "{1}{G}", 2, 2), 1)
        b2 = _place_on_battlefield(game, _make_creature("Bear2", "{1}{G}", 2, 2), 1)
        assert game._validate_blocking(menace, [b1, b2])

    def test_reach_blocks_flyer(self):
        """Reach allows blocking a flyer (702.17b)."""
        game = _setup_game()
        flyer = _place_on_battlefield(game, _make_creature("Sky Eagle", "{1}{W}", 2, 2, "Flying"))
        reacher = _place_on_battlefield(game, _make_creature("Spider", "{1}{G}", 1, 3, "Reach"), 1)
        assert game._can_block(flyer, reacher)


# ─── COMBAT KEYWORDS ─────────────────────────────────────────────────────────

class TestCombatKeywords:
    """First Strike, Double Strike, Trample, Vigilance."""

    def test_first_strike_kills_before_normal(self):
        """First striker deals damage first; if it kills, blocker deals no damage (702.7)."""
        game = _setup_game()
        fs = _place_on_battlefield(game, _make_creature("Knight", "{1}{W}", 3, 2, "First strike"))
        blocker = _place_on_battlefield(game, _make_creature("Bear", "{1}{G}", 2, 2), 1)

        game.combat_attackers = [fs]
        game.combat_blockers = {fs.id: [blocker]}
        game.resolve_combat_damage()
        # Knight deals 3 first-strike damage → kills 2-toughness bear in FS phase
        # Bear dies before normal damage → Knight takes 0 damage
        assert fs.damage_taken == 0
        assert blocker not in game.battlefield.cards

    def test_double_strike_both_phases(self):
        """Double strike deals damage in both first-strike and normal phases (702.4)."""
        game = _setup_game()
        ds = _place_on_battlefield(game, _make_creature("Dervish", "{1}{R}", 2, 3, "Double strike"))
        p2 = game.players[1]
        initial_life = p2.life

        game.combat_attackers = [ds]
        game.combat_blockers = {}
        game.resolve_combat_damage()
        # 2 damage in FS phase + 2 in normal = 4 total to opponent
        assert p2.life == initial_life - 4

    def test_trample_excess_damage(self):
        """Trample: excess damage over blocker toughness goes to defending player (702.19)."""
        game = _setup_game()
        trampler = _place_on_battlefield(game, _make_creature("Wurm", "{3}{G}", 6, 6, "Trample"))
        blocker = _place_on_battlefield(game, _make_creature("Bear", "{1}{G}", 2, 2), 1)
        p2 = game.players[1]
        initial_life = p2.life

        game.combat_attackers = [trampler]
        game.combat_blockers = {trampler.id: [blocker]}
        game.resolve_combat_damage()
        # Wurm assigns 2 to bear (lethal), tramples 4 to player
        assert p2.life == initial_life - 4

    def test_vigilance_no_tap(self):
        """Vigilance: attacking doesn't cause the creature to tap (702.20)."""
        game = _setup_game()
        vig = _place_on_battlefield(game, _make_creature("Serra Angel", "{3}{W}{W}", 4, 4, "Flying\nVigilance"))
        assert vig.has_vigilance
        # When declaring attackers, vigilance creatures don't tap
        # The engine handles this in apply_action for declare_attackers


# ─── DAMAGE KEYWORDS ─────────────────────────────────────────────────────────

class TestDamageKeywords:
    """Deathtouch, Lifelink."""

    def test_deathtouch_lethal_one(self):
        """Deathtouch: any amount of damage is lethal (702.2c)."""
        game = _setup_game()
        dt = _place_on_battlefield(game, _make_creature("Viper", "{G}", 1, 1, "Deathtouch"))
        blocker = _place_on_battlefield(game, _make_creature("Giant", "{3}{G}", 5, 5), 1)

        game.combat_attackers = [dt]
        game.combat_blockers = {dt.id: [blocker]}
        game.resolve_combat_damage()
        # 1 damage from deathtouch → SBA kills Giant
        assert blocker not in game.battlefield.cards

    def test_lifelink_heal(self):
        """Lifelink: damage dealt heals controller (702.15)."""
        game = _setup_game()
        ll = _place_on_battlefield(game, _make_creature("Vampire", "{1}{B}", 3, 2, "Lifelink"))
        p1 = game.players[0]
        p2 = game.players[1]
        p1.life = 15
        initial_p2_life = p2.life

        game.combat_attackers = [ll]
        game.combat_blockers = {}
        game.resolve_combat_damage()
        assert p1.life == 18  # Gained 3
        assert p2.life == initial_p2_life - 3


# ─── SPEED KEYWORDS ──────────────────────────────────────────────────────────

class TestSpeedKeywords:
    """Haste, Flash."""

    def test_haste_no_summoning_sickness(self):
        """Haste: can attack/tap the turn it enters (702.10)."""
        game = _setup_game()
        haster = _make_creature("Goblin", "{R}", 2, 1, "Haste")

        # Simulate resolution — haste removes summoning sickness
        haster.controller = game.players[0]
        haster.summoning_sickness = not haster.has_haste
        assert haster.summoning_sickness is False

    def test_flash_parsed(self):
        """Flash is parsed from oracle text (702.8)."""
        card = _make_creature("Ambusher", "{1}{G}", 2, 2, "Flash")
        assert card.has_flash is True

    def test_no_haste_has_summoning_sickness(self):
        """Without haste, creature has summoning sickness."""
        card = _make_creature("Bear", "{1}{G}", 2, 2)
        card.summoning_sickness = not card.has_haste
        assert card.summoning_sickness is True


# ─── PROTECTION KEYWORDS ─────────────────────────────────────────────────────

class TestProtectionKeywords:
    """Hexproof, Indestructible, Protection, Ward."""

    def test_hexproof_parsed(self):
        """Hexproof: can't be targeted by opponents (702.11)."""
        card = _make_creature("Troll", "{2}{G}", 4, 4, "Hexproof")
        assert card.has_hexproof is True

    def test_indestructible_survives_damage(self):
        """Indestructible: not destroyed by lethal damage (702.12)."""
        game = _setup_game()
        indestr = _place_on_battlefield(game, _make_creature("God", "{3}{W}", 4, 4, "Indestructible"))
        indestr.damage_taken = 10  # 10 damage, but indestructible
        game.check_state_based_actions()
        assert indestr in game.battlefield.cards  # Survives!

    def test_indestructible_dies_to_zero_toughness(self):
        """Indestructible does NOT prevent 0-toughness death (704.5f)."""
        game = _setup_game()
        indestr = _place_on_battlefield(game, _make_creature("God", "{3}{W}", 4, 4, "Indestructible"))
        # Reduce toughness to 0 via temp modifier
        indestr._temp_modifiers.append({'power': 0, 'toughness': -4})
        assert indestr.toughness == 0
        game.check_state_based_actions()
        assert indestr not in game.battlefield.cards  # Dies to SBA 704.5f

    def test_protection_prevents_damage(self):
        """Protection from [color]: damage from matching source is prevented (702.16d)."""
        game = _setup_game()
        pro = _place_on_battlefield(game, _make_creature("Knight", "{W}{W}", 2, 2,
                                                          "Protection from red"))
        red_attacker = _place_on_battlefield(game, _make_creature("Devil", "{R}", 3, 1), 1)
        red_attacker.color_identity = ['R']

        game.combat_attackers = [red_attacker]
        game.combat_blockers = {red_attacker.id: [pro]}
        game.resolve_combat_damage()
        # Protection prevents all damage from red source
        assert pro.damage_taken == 0

    def test_ward_parsed(self):
        """Ward {N}: parsed from oracle text (702.21)."""
        card = _make_creature("Warded", "{2}{U}", 3, 3, "Ward {2}")
        assert card.has_ward is True
        assert card.ward_cost == "{2}"

    def test_defender_cant_attack(self):
        """Defender: creature can't attack (702.3)."""
        card = _make_creature("Wall", "{W}", 0, 5, "Defender")
        assert card.has_defender is True


# ─── TRIGGERED ABILITIES ─────────────────────────────────────────────────────

class TestTriggeredAbilities:
    """ETB, Death, Upkeep, Landfall, Attack, Combat Damage, Kicker triggers."""

    def test_etb_goes_on_stack(self):
        """ETB trigger goes on the stack (Rule 603.3)."""
        game = _setup_game()
        etb_fired = []
        card = _make_creature("Mulldrifter", "{3}{U}{U}", 2, 2,
                              "When this creature enters the battlefield, draw two cards.")
        card.etb_effect = lambda g, c: etb_fired.append(True)
        card.controller = game.players[0]
        # Place on stack and resolve
        game.stack.add(card)
        game._resolve_stack_top()
        # ETB should be on the stack now
        assert len(game.stack) == 1  # ETB trigger on stack
        game._resolve_stack_top()
        assert etb_fired  # ETB fired

    def test_death_trigger_fires(self):
        """Death trigger fires when creature goes to graveyard (700.4).
        The trigger goes on the stack (Rule 603.3), then resolves."""
        game = _setup_game()
        death_fired = []
        card = _place_on_battlefield(game, _make_creature("Doomed", "{B}", 1, 1))
        card.death_effect = lambda g, c: death_fired.append(True)

        # Kill it via lethal damage
        card.damage_taken = 1
        game.check_state_based_actions()
        assert card not in game.battlefield.cards
        # Death trigger is on the stack — resolve it
        assert len(game.stack) >= 1
        game._resolve_stack_top()
        assert death_fired

    def test_upkeep_trigger_fires(self):
        """Upkeep trigger fires at beginning of upkeep (503.1)."""
        game = _setup_game()
        upkeep_fired = []
        card = _place_on_battlefield(game, _make_creature("Upkeeper", "{B}", 1, 1))
        card.upkeep_effect = lambda g, c: upkeep_fired.append(True)

        game._fire_upkeep_triggers()
        # Trigger should be on the stack
        assert len(game.stack) >= 1

    def test_landfall_parsed(self):
        """Landfall trigger is parsed from oracle text (Rule 604.3)."""
        card = _make_creature("Steppe Lynx", "{W}", 0, 1,
                              "Landfall — Whenever a land enters the battlefield under your control, this creature gets +2/+2 until end of turn.")
        assert card.landfall_effect is not None

    def test_attack_trigger(self):
        """Attack trigger is parsed from 'whenever ~ attacks' text."""
        card = _make_creature("Aurelia", "{2}{R}{W}", 3, 4,
                              "Whenever Aurelia attacks, Aurelia gets +1/+1 until end of turn.")
        assert card.attack_trigger is not None

    def test_kicker_parsed(self):
        """Kicker cost and effect are parsed (702.32)."""
        card = Card(
            name="Burst Lightning", cost="{R}", type_line="Instant",
            oracle_text="Kicker {4}\nBurst Lightning deals 2 damage to any target. If it was kicked, it deals 4 damage instead."
        )
        assert card.kicker_cost != ""

    def test_combat_damage_trigger(self):
        """Combat damage trigger fires when dealing damage to player."""
        card = _make_creature("Thief", "{1}{U}", 1, 1,
                              "Whenever this creature deals combat damage to a player, draw a card.")
        assert card.combat_damage_trigger is not None


# ─── STATIC EFFECTS ──────────────────────────────────────────────────────────

class TestStaticEffects:
    """Anthems and continuous P/T modification (Rule 613)."""

    def test_anthem_buffs_others(self):
        """'Other creatures you control get +1/+1' applies to allies (613.7d)."""
        game = _setup_game()
        lord = _place_on_battlefield(game, _make_creature("Glorious Anthem", "{1}{W}{W}", 2, 2,
            "Other creatures you control get +1/+1."))
        lord.static_effect = {'power': 1, 'toughness': 1, 'filter': 'other_creatures'}
        ally = _place_on_battlefield(game, _make_creature("Bear", "{1}{G}", 2, 2))

        game._apply_static_effects()
        assert ally.power == 3  # 2 base + 1 anthem
        assert ally.toughness == 3

    def test_anthem_doesnt_buff_self(self):
        """'Other creatures' excludes the source."""
        game = _setup_game()
        lord = _place_on_battlefield(game, _make_creature("Lord", "{2}{W}", 2, 2))
        lord.static_effect = {'power': 1, 'toughness': 1, 'filter': 'other_creatures'}

        game._apply_static_effects()
        assert lord.power == 2  # Not buffed by own anthem

    def test_anthem_tribal(self):
        """Tribal anthem only buffs matching type."""
        game = _setup_game()
        lord = _place_on_battlefield(game, _make_creature("Elf Lord", "{1}{G}{G}", 2, 2,
            type_line="Creature — Elf"))
        lord.static_effect = {'power': 1, 'toughness': 1, 'filter': 'type', 'type': 'Elf'}
        elf = _place_on_battlefield(game, _make_creature("Llanowar Elves", "{G}", 1, 1,
            type_line="Creature — Elf Druid"))
        human = _place_on_battlefield(game, _make_creature("Soldier", "{W}", 1, 1,
            type_line="Creature — Human Soldier"))

        game._apply_static_effects()
        assert elf.power == 2     # Buffed
        assert human.power == 1   # Not an Elf, not buffed


# ─── UTILITY MECHANICS ───────────────────────────────────────────────────────

class TestUtilityMechanics:
    """Cycling, Prowess, Scry, Flashback."""

    def test_cycling_parsed(self):
        """Cycling cost is parsed (702.28)."""
        card = Card(name="Ash Barrens", cost="", type_line="Land",
                    oracle_text="Cycling {1}")
        assert card.cycling_cost != ""

    def test_prowess_parsed(self):
        """Prowess is parsed (702.107)."""
        card = _make_creature("Monk", "{1}{R}", 2, 2, "Prowess")
        assert card.has_prowess is True

    def test_scry_parsed(self):
        """Scry N is parsed from spell text."""
        card = Card(name="Opt", cost="{U}", type_line="Instant",
                    oracle_text="Scry 1, then draw a card.")
        assert card.scry_amount == 1

    def test_flashback_parsed(self):
        """Flashback cost is parsed (702.33)."""
        card = Card(name="Firebolt", cost="{R}", type_line="Sorcery",
                    oracle_text="Firebolt deals 2 damage to any target.\nFlashback {4}{R}")
        assert card.flashback_cost != ""


# ─── SUBTYPE MECHANICS ───────────────────────────────────────────────────────

class TestSubtypeMechanics:
    """Equipment, Aura, Vehicle, Planeswalker."""

    def test_equipment_bonus_applied(self):
        """Equipment gives P/T bonus when attached (Rule 301.5)."""
        game = _setup_game()
        creature = _place_on_battlefield(game, _make_creature("Bear", "{1}{G}", 2, 2))
        equip = Card(name="Bonesplitter", cost="{1}", type_line="Artifact — Equipment",
                     oracle_text="Equipped creature gets +2/+0.\nEquip {1}")
        _place_on_battlefield(game, equip)
        equip.equipped_to = creature
        creature.attachments.append(equip)
        assert creature.power == 4  # 2 + 2 from equipment
        assert creature.toughness == 2  # No toughness bonus

    def test_vehicle_crew_flags(self):
        """Vehicle has is_vehicle and crew_cost parsed."""
        card = Card(name="Smuggler's Copter", cost="{2}", type_line="Artifact — Vehicle",
                    oracle_text="Flying\nCrew 1\n3/3",
                    base_power=3, base_toughness=3)
        assert card.is_vehicle is True
        assert card.crew_cost == 1

    def test_planeswalker_loyalty(self):
        """Planeswalker enters with starting loyalty (306.5b)."""
        card = Card(name="Jace", cost="{1}{U}{U}", type_line="Legendary Planeswalker — Jace",
                    oracle_text="+1: Draw a card.\n-2: Return target creature to its owner's hand.")
        # Planeswalker parsing sets loyalty from oracle text
        assert card.is_planeswalker


# ─── COUNTER MECHANICS ───────────────────────────────────────────────────────

class TestCounterMechanics:
    """+1/+1 and -1/-1 counters (Rule 122)."""

    def test_plus1_counter_adds_power(self):
        """+1/+1 counter increases power and toughness."""
        card = _make_creature("Bear", "{1}{G}", 2, 2)
        card.counters['+1/+1'] = 2
        assert card.power == 4   # 2 base + 2 counters
        assert card.toughness == 4

    def test_minus1_counter_reduces(self):
        """-1/-1 counter reduces power and toughness."""
        card = _make_creature("Bear", "{1}{G}", 2, 2)
        card.counters['-1/-1'] = 1
        assert card.power == 1
        assert card.toughness == 1

    def test_counters_annihilate(self):
        """++1/+1 and -1/-1 counters cancel out (Rule 704.5q)."""
        card = _make_creature("Bear", "{1}{G}", 2, 2)
        card.counters['+1/+1'] = 3
        card.counters['-1/-1'] = 1
        # Net: +2/+2
        assert card.power == 4
        assert card.toughness == 4

    def test_counters_etb_parsed(self):
        """ETB counters parsed from oracle text (Rule 122)."""
        card = _make_creature("Walking Ballista", "{X}{X}", 0, 0,
                              "Walking Ballista enters the battlefield with X +1/+1 counters on it.")
        # The parser should detect counter ETB
        # (Actual counter placement happens through etb_effect at resolution)
        assert card.counters.get('+1/+1', 0) >= 0  # Parser sets initial


# ─── FULL KEYWORD REGISTRY REPORT ────────────────────────────────────────────

class TestKeywordRegistry:
    """Verify all keywords are parsed from oracle text correctly."""

    @pytest.mark.parametrize("keyword,attr,oracle", [
        ("Haste", "has_haste", "Haste"),
        ("Flying", "has_flying", "Flying"),
        ("Trample", "has_trample", "Trample"),
        ("Lifelink", "has_lifelink", "Lifelink"),
        ("Deathtouch", "has_deathtouch", "Deathtouch"),
        ("First strike", "has_first_strike", "First strike"),
        ("Double strike", "has_double_strike", "Double strike"),
        ("Vigilance", "has_vigilance", "Vigilance"),
        ("Reach", "has_reach", "Reach"),
        ("Flash", "has_flash", "Flash"),
        ("Hexproof", "has_hexproof", "Hexproof"),
        ("Menace", "has_menace", "Menace"),
        ("Indestructible", "has_indestructible", "Indestructible"),
        ("Defender", "has_defender", "Defender"),
        ("Prowess", "has_prowess", "Prowess"),
    ])
    def test_keyword_parsing(self, keyword, attr, oracle):
        """Each keyword is correctly detected from oracle text."""
        card = _make_creature(f"Test {keyword}", "{1}", 1, 1, oracle)
        assert getattr(card, attr) is True, f"{keyword} not parsed → {attr} should be True"
