"""Card — MTG card model with Oracle text parsing and effect generation.

The Card class represents a Magic: The Gathering card with full keyword
support and automatic Oracle text parsing.  On construction, the card's
oracle_text is analyzed to detect:

    Keywords (27): flying, trample, deathtouch, lifelink, first_strike,
        double_strike, haste, reach, vigilance, menace, hexproof, flash,
        indestructible, defender, prowess, protection, ward, cascade,
        convoke, cycling, fight, proliferate, mill, bounce, sac_damage,
        crew, equip

    Effects: ETB triggers, death triggers, upkeep triggers, damage spells,
        removal, card draw, buff/pump, counter-spells, board wipes, token
        generation (Treasure), loyalty abilities (planeswalkers)

    Mana production: detected from Oracle text for lands and mana dorks

The StackItem dataclass represents a spell or ability on the MTG stack
awaiting resolution.
"""

from typing import List, Optional, Any, Dict, Callable, TYPE_CHECKING
from dataclasses import dataclass, field
import re
import copy
import itertools
import random

if TYPE_CHECKING:
    from .game import Game

_card_id_counter = itertools.count(1)


@dataclass
class StackItem:
    """Represents a triggered/activated ability on the stack (not a card).
    Allows ETB triggers, death triggers, upkeep triggers, etc. to use the stack
    so opponents can respond (Rule 603.3)."""
    effect: Callable[['Game', Any], None]
    source: Any  # The Card that created this trigger
    controller: Any  # The Player who controls this trigger
    description: str = ""

    # Duck-type to look like a Card for stack processing
    is_creature: bool = False
    is_instant: bool = False
    is_sorcery: bool = False
    is_land: bool = False
    name: str = ""
    cost: str = ""
    type_line: str = "Ability"
    id: int = 0

    def __post_init__(self):
        if not self.name:
            self.name = self.description


@dataclass
class Card:
    name: str
    cost: str  # Mana cost e.g., "{1}{R}"
    type_line: str
    oracle_text: str = ""
    base_power: Optional[int] = None
    base_toughness: Optional[int] = None
    
    # Color and mana info
    color_identity: List[str] = field(default_factory=list)
    produced_mana: List[str] = field(default_factory=list)
    
    # Format Meta
    canadian_highlander_points: int = 0
    
    # Dynamic attributes for gameplay
    controller: Any = None
    id: int = field(default_factory=lambda: next(_card_id_counter))
    effect: Optional[Callable[['Game', 'Card'], None]] = None
    etb_effect: Optional[Callable[['Game', 'Card'], None]] = None  # Enter-the-battlefield
    
    # Temporary modifiers (cleared at cleanup, Rule 514.2)
    # Each entry: {'power': int, 'toughness': int}
    _temp_modifiers: List[Dict[str, int]] = field(default_factory=list)
    
    # State flags
    tapped: bool = False
    summoning_sickness: bool = False
    damage_taken: int = 0  # Track damage for toughness-based death (Rule 120.6)
    deathtouch_damaged: bool = False  # Track deathtouch damage for SBAs (Rule 704.5h)
    
    # Keywords — all parsed from oracle_text
    has_haste: bool = False
    has_flying: bool = False
    has_trample: bool = False
    has_lifelink: bool = False
    has_deathtouch: bool = False
    has_first_strike: bool = False
    has_double_strike: bool = False
    has_vigilance: bool = False
    has_reach: bool = False
    has_flash: bool = False
    has_hexproof: bool = False
    has_menace: bool = False
    has_indestructible: bool = False
    has_defender: bool = False
    
    # Missing Keyword Fields
    has_cascade: bool = False
    has_delve: bool = False
    has_affinity: bool = False
    has_annihilator: bool = False
    has_suspend: bool = False
    has_dredge: bool = False
    has_shadow: bool = False
    has_fear: bool = False
    has_intimidate: bool = False
    has_skulk: bool = False
    is_unblockable: bool = False
    
    # Batch 1: Death triggers
    has_undying: bool = False       # Rule 702.92 — return with +1/+1 if no +1/+1
    has_persist: bool = False       # Rule 702.78 — return with -1/-1 if no -1/-1
    
    # Batch 2: Combat keywords
    has_infect: bool = False        # Rule 702.89 — poison + -1/-1 counters
    has_toxic: bool = False         # Rule 702.164 — poison on combat damage
    toxic_count: int = 0            # Toxic N
    has_exalted: bool = False       # Rule 702.82 — +1/+1 when attacking alone
    has_battle_cry: bool = False    # Rule 702.90 — +1/+0 to other attackers
    has_flanking: bool = False      # Rule 702.24 — -1/-1 to non-flanking blockers
    has_bushido: bool = False       # Rule 702.44 — +N/+N when blocked
    bushido_count: int = 0          # Bushido N
    has_wither: bool = False        # Rule 702.79 — damage as -1/-1 counters
    
    # Batch 3: Keyword actions
    surveil_amount: int = 0         # Surveil N
    is_investigate: bool = False    # Create Clue token
    is_explore: bool = False        # Reveal top, land→hand or +1/+1
    is_connive: bool = False        # Draw+discard+counter
    amass_count: int = 0            # Amass N
    amass_type: str = ''            # 'Zombies', 'Orcs'
    
    # Batch 4: Alt costs
    evoke_cost: str = ''            # Evoke {cost}
    unearth_cost: str = ''          # Unearth {cost}
    
    # Lorwyn Eclipsed
    has_blight: bool = False
    has_vivid: bool = False
    
    # MTG Foundations (Core Set explicit mappings)
    has_flashback: bool = False
    has_convoke: bool = False
    has_prowess: bool = False
    offspring_cost: str = ""
    was_offspring_paid: bool = False
    has_protection_from: List[str] = field(default_factory=list)  # e.g. ['white', 'blue']
    has_ward: bool = False
    ward_cost: str = ""  # e.g. "{2}"
    enters_tapped: bool = False  # "enters the battlefield tapped"
    has_x_cost: bool = False  # Cost contains {X}
    flashback_cost: str = ""  # Flashback {cost}
    scry_amount: int = 0  # Scry N
    from_graveyard: bool = False  # True when being cast via flashback
    _cant_attack: bool = False  # "can't attack" / "can't attack unless"
    _cant_block: bool = False  # "can't block" / "can't block unless"
    
    # +1/+1 counters (Rule 122)
    counters: Dict[str, int] = field(default_factory=dict)  # e.g. {'+1/+1': 2}
    
    # Trigger effects
    upkeep_effect: Optional[Callable[['Game', 'Card'], None]] = None
    death_effect: Optional[Callable[['Game', 'Card'], None]] = None
    
    # Static/continuous effects (anthems)
    static_effect: Optional[Dict[str, Any]] = None  # e.g. {'power': 1, 'toughness': 1, 'filter': 'creature'}
    
    # Activated abilities: list of {cost_desc, cost_tap, effect, description}
    activated_abilities: List[Dict[str, Any]] = field(default_factory=list)
    
    # Token generation
    token_effect: Optional[Callable[['Game', 'Card'], None]] = None

    # --- Tier 2: Equipment & Auras ---
    equip_cost: str = ""  # "{N}" for Equipment
    equip_bonus: Dict[str, int] = field(default_factory=dict)  # {'power': 2, 'toughness': 0}
    equipped_to: Optional['Card'] = None  # Creature this is attached to
    is_equipment: bool = False
    
    is_aura: bool = False
    enchant_target_type: str = ""  # "creature", "permanent", etc.
    enchanted_to: Optional['Card'] = None  # Permanent this is attached to
    
    # --- Tier 4: Planeswalkers ---
    loyalty: int = 0  # Starting / current loyalty counters
    loyalty_abilities: List[Dict[str, Any]] = field(default_factory=list)  # [{cost, effect, description}]
    loyalty_used_this_turn: bool = False  # One activation per turn
    
    # --- Tier 5: Advanced Mechanics ---
    landfall_effect: Optional[Callable[['Game', 'Card'], None]] = None  # Landfall trigger
    attack_trigger: Optional[Callable[['Game', 'Card'], None]] = None  # "whenever ~ attacks"
    combat_damage_trigger: Optional[Callable[['Game', 'Card'], None]] = None  # "deals combat damage"
    cast_trigger: Optional[Callable[['Game', 'Card'], None]] = None  # "whenever you cast"
    block_trigger: Optional[Callable[['Game', 'Card'], None]] = None  # "whenever ~ blocks"
    is_mana_dork: bool = False  # {T}: Add mana
    tap_ability_effect: Optional[Callable[['Game', 'Card'], None]] = None  # {T}: activated
    enchantment_trigger: Optional[Callable[['Game', 'Card'], None]] = None  # enchantment whenever/upkeep
    has_protection: bool = False  # protection from X
    has_self_pump: bool = False  # conditional self-buff
    self_pump_power: int = 0  # maximum pump power bonus
    self_pump_toughness: int = 0  # maximum pump toughness bonus
    sacrifice_effect: Optional[Callable[['Game', 'Card'], None]] = None  # sacrifice-based ability
    has_restriction: bool = False  # can't/don't text
    broad_trigger: Optional[Callable[['Game', 'Card'], None]] = None  # catch-all trigger
    has_activated_ability: bool = False  # non-tap activated abilities
    has_text_ability: bool = False  # generic text ability (catch-all)
    is_vanilla: bool = False  # True vanilla creature (no abilities, just P/T)
    has_morph: bool = False  # morph/megamorph/disguise
    kicker_cost: str = ""  # Kicker {cost}
    kicker_effect: Optional[Callable[['Game', 'Card'], None]] = None  # Enhanced effect when kicked
    was_kicked: bool = False  # True if this spell was cast with kicker
    
    # --- Tier 6: Utility Mechanics ---
    cycling_cost: str = ""  # Cycling {cost}
    is_mill: bool = False  # Mill spell hint
    is_fight: bool = False  # Fight spell hint
    is_proliferate: bool = False  # Proliferate spell hint
    
    # --- Tier 7: Combat & Economy ---
    is_vehicle: bool = False  # Vehicle artifact
    crew_cost: int = 0  # Crew N — tap creatures with total power >= N
    is_crewed: bool = False  # Currently crewed this turn
    has_prowess: bool = False  # Prowess keyword
    is_bounce: bool = False  # Bounce spell hint
    is_treasure: bool = False  # Treasure token
    
    # AI Hints
    is_removal: bool = False
    is_burn: bool = False
    is_draw: bool = False
    is_counter: bool = False
    is_buff: bool = False
    is_lifegain: bool = False
    is_board_wipe: bool = False
    has_drawback: bool = False
    is_discard: bool = False
    requires_creature_sacrifice: bool = False  # Additional cost: sacrifice a creature
    is_modal: bool = False  # "Choose one" / "Choose two" spells
    modal_modes: list = field(default_factory=list)  # List of {desc, effect}
    madness_cost: str = ""  # Madness {cost} — cast for this cost when discarded
    emerge_cost: str = ""  # Emerge {cost} — sacrifice creature to reduce cost
    
    # Characteristic-Defining Ability (Layer 7a, CR 604.3)
    # Stores CDA type for dynamic P/T computation by the LayerEngine.
    # Examples: 'deaths_shadow', 'tarmogoyf', 'serra_avatar', 'scourge_skyclaves'
    cda_type: str = ""  # Set by _parse_cda
    
    # Synergy tags
    creature_types: List[str] = field(default_factory=list)
    
    # Reverse link for attachments (so creature knows what's on it)
    attachments: List['Card'] = field(default_factory=list)

    def __post_init__(self):
        lower_text = self.oracle_text.lower()
        
        # Backward compat: accept 'power'/'toughness' kwargs and map to base_
        # (deepcopy and Card(..., power=X) both work)
        if self.base_power is None and hasattr(self, '_power_init'):
            self.base_power = self._power_init
        if self.base_toughness is None and hasattr(self, '_toughness_init'):
            self.base_toughness = self._toughness_init
        
        # Parse keywords — use word-boundary regex to avoid false positives
        # Includes MTG Foundations (FDN) explicit mechanics mapped here
        keywords = {
            'haste': 'has_haste', 'flying': 'has_flying', 'trample': 'has_trample',
            'lifelink': 'has_lifelink', 'deathtouch': 'has_deathtouch',
            'first strike': 'has_first_strike', 'double strike': 'has_double_strike',
            'vigilance': 'has_vigilance', 'reach': 'has_reach',
            'flash': 'has_flash', 'hexproof': 'has_hexproof',
            'menace': 'has_menace', 'indestructible': 'has_indestructible',
            'defender': 'has_defender', 
            
            # Lorwyn Eclipsed
            'blight': 'has_blight', 'vivid': 'has_vivid',
            
            # MTG Foundations explicit mappings
            'flashback': 'has_flashback', 'convoke': 'has_convoke', 'prowess': 'has_prowess',
            
            # Tier 3: Archetype-enabling keywords
            'cascade': 'has_cascade',       # Rule 702.84 — free spell chain
            'delve': 'has_delve',           # Rule 702.65 — exile graveyard for mana
            'affinity': 'has_affinity',     # Rule 702.40 — cost reduction
            'annihilator': 'has_annihilator', # Rule 702.85 — Eldrazi forced sacrifice
            'suspend': 'has_suspend',       # Rule 702.61 — time counter exile
            'dredge': 'has_dredge',         # Rule 702.51 — mill to recur
            'shadow': 'has_shadow',         # Rule 702.27 — can only block/be blocked by shadow
            'fear': 'has_fear',             # Rule 702.35 — evasion (artifact/black)
            'intimidate': 'has_intimidate', # Rule 702.13 — evasion (artifact/color)
            'skulk': 'has_skulk',           # Rule 702.119 — can't be blocked by higher power
            
            # Batch 1: Death triggers
            'undying': 'has_undying',       # Rule 702.92
            'persist': 'has_persist',       # Rule 702.78
            
            # Batch 2: Combat keywords
            'infect': 'has_infect',         # Rule 702.89
            'toxic': 'has_toxic',           # Rule 702.164
            'exalted': 'has_exalted',       # Rule 702.82
            'battle cry': 'has_battle_cry', # Rule 702.90
            'flanking': 'has_flanking',     # Rule 702.24
            'bushido': 'has_bushido',       # Rule 702.44
            'wither': 'has_wither',         # Rule 702.79
        }
        for kw, attr in keywords.items():
            if re.search(r'\b' + re.escape(kw) + r'\b', lower_text):
                setattr(self, attr, True)
                
        # Save base attributes for CR 613 Layer 6 (Ability) recalculation
        self._base_keywords = {attr: getattr(self, attr) for attr in keywords.values()}
        
        # Parse creature types from type_line
        if 'Creature' in self.type_line:
            parts = self.type_line.split('—')
            if len(parts) > 1:
                self.creature_types = [t.strip() for t in parts[1].split()]
                
        # Save base types for CR 613 Layer 4 (Types) recalculation
        self._base_creature_types = list(self.creature_types)
        
        # --- NEW: Parse additional mechanics ---
        self._parse_enters_tapped(lower_text)
        self._parse_x_cost()
        self._parse_protection(lower_text)
        self._parse_ward(lower_text)
        self._parse_counters_etb(lower_text)
        self._parse_restrictions(lower_text)
        self._parse_upkeep_trigger(lower_text)
        self._parse_death_trigger(lower_text)
        self._parse_static_effect(lower_text)
        self._parse_cda(lower_text)
        self._parse_activated_abilities(lower_text)
        self._parse_token_effect(lower_text)
        self._parse_equipment(lower_text)
        self._parse_aura(lower_text)
        self._parse_flashback(lower_text)
        self._parse_scry(lower_text)
        self._parse_planeswalker(lower_text)
        self._parse_landfall(lower_text)
        self._parse_attack_trigger(lower_text)
        self._parse_combat_damage_trigger(lower_text)
        self._parse_cast_trigger(lower_text)
        self._parse_block_trigger(lower_text)
        self._parse_mana_dork(lower_text)
        self._parse_tap_ability(lower_text)
        self._parse_enchantment_trigger(lower_text)
        self._parse_protection(lower_text)
        self._parse_self_pump(lower_text)
        self._parse_sacrifice_ability(lower_text)
        self._parse_creature_restriction(lower_text)
        self._parse_artifact_tap(lower_text)
        self._parse_broad_etb(lower_text)
        self._parse_broad_whenever(lower_text)
        self._parse_creature_upkeep(lower_text)
        self._parse_activated_ability(lower_text)
        self._parse_broad_enchantment(lower_text)
        self._parse_creature_text_fallback(lower_text)
        self._parse_spell_target_fallback(lower_text)
        self._parse_artifact_other(lower_text)
        self._parse_morph(lower_text)
        self._parse_planeswalker_fallback(lower_text)
        self._parse_kicker(lower_text)
        self._parse_cycling(lower_text)
        self._parse_vehicle(lower_text)
        self._parse_spacecraft(lower_text)
        self._parse_class(lower_text)
        self._parse_marvel(lower_text)
        self._parse_tmnt(lower_text)
        self._parse_prowess(lower_text)
        self._parse_offspring(lower_text)
        self._parse_modal(lower_text)
        self._parse_cascade(lower_text)
        self._parse_delve(lower_text)
        self._parse_affinity(lower_text)
        self._parse_annihilator(lower_text)
        self._parse_dredge(lower_text)
        self._parse_unblockable(lower_text)
        self._parse_madness(lower_text)
        self._parse_convoke_mechanic(lower_text)
        self._parse_emerge(lower_text)
        self._parse_toxic_count(lower_text)
        self._parse_bushido_count(lower_text)
        self._parse_surveil(lower_text)
        self._parse_investigate_keyword(lower_text)
        self._parse_explore_keyword(lower_text)
        self._parse_connive_keyword(lower_text)
        self._parse_amass_keyword(lower_text)
        self._parse_evoke(lower_text)
        self._parse_unearth(lower_text)
        
        # Parse land abilities (dual/shock/pain/fetch)
        self._parse_land_abilities(lower_text)
        
        # Parse effects (spells + ETB)
        self.parse_effects()
        self._parse_broad_spell(lower_text)
        self.parse_etb_effects()
        
        # Apply remote mechanics (Lorwyn Eclipsed)
        try:
            from engine.mechanics.lorwyn_eclipsed import process_lorwyn_eclipsed_mechanics
            process_lorwyn_eclipsed_mechanics(self)
        except ImportError:
            pass
        
        # ETB sacrifice MUST run AFTER parse_etb_effects so it can chain
        # with (not overwrite) any ETB effect that was parsed
        self._parse_etb_sacrifice(lower_text)
        
        # === BROAD TEXT ABILITY PARSER ===
        # Catches top-frequency patterns that fall through existing parsers:
        # tap abilities on non-creatures, broader ETB, tokens, bounce/reanimate
        self._parse_broad_text_abilities(lower_text)
        
        # Second sweep: catches remaining cards that fell through all parsers
        self._parse_remaining_text(lower_text)
        
        # === FINAL COVERAGE CATCH-ALL ===
        # Ensure every non-land card is "covered" by at least one flag
        self._parse_final_coverage(lower_text)
    
    # ─── NEW PARSERS ──────────────────────────────────────────────
    
    def _parse_offspring(self, text: str):
        """Parse 'Offspring {cost}' (Rule 702.175)."""
        offspring_match = re.search(r'offspring (\{[^\}]+\})', text)
        if offspring_match:
            self.offspring_cost = offspring_match.group(1).upper()
            
            def offspring_etb(game, card):
                if card.was_offspring_paid:
                    import copy
                    token = copy.deepcopy(card)
                    token.name = f"{card.name} Token"
                    token.type_line = f"Token {card.type_line}"
                    token.base_power = 1
                    token.base_toughness = 1
                    token.cost = "" # Tokens have no mana cost
                    token.summoning_sickness = True
                    token.was_offspring_paid = False # Prevent infinite token loop
                    from engine.card import _card_id_counter
                    token.id = next(_card_id_counter)
                    game.battlefield.add(token)
                    game.log_event(f"Offspring: {card.name} creates a 1/1 token copy")
            
            existing_etb = self.etb_effect
            if existing_etb:
                def combined_etb(game, card, old=existing_etb, os_etb=offspring_etb):
                    old(game, card)
                    os_etb(game, card)
                self.etb_effect = combined_etb
            else:
                self.etb_effect = offspring_etb

    def _parse_modal(self, text: str):
        """Parse 'Choose one/two/three' modal spells (Rule 700.2).
        Splits modes by bullet (•) and creates per-mode effects."""
        modal_match = re.search(r'choose (one|two|three)\b', text)
        if not modal_match:
            return
        
        self.is_modal = True
        num_choices_str = modal_match.group(1)
        
        # Split Oracle text into modes by bullet character
        parts = text.split('•')
        if len(parts) < 2:
            # No bullets found — try newline splitting
            parts = text.split('\\n')
        
        modes = []
        for i, part in enumerate(parts[1:], 1):  # Skip the "Choose one —" preamble
            mode_text = part.strip()
            if not mode_text:
                continue
            
            mode = {'index': i - 1, 'description': mode_text, 'effect': None}
            
            # Parse each mode's effect
            dmg = re.search(r'deals? (\d+) damage', mode_text)
            draw = re.search(r'draw (\d+|a|two|three) cards?', mode_text)
            life = re.search(r'gain (\d+) life', mode_text)
            destroy = re.search(r'destroy target (creature|artifact|enchantment)', mode_text)
            buff = re.search(r'gets? \+(\d+)/\+(\d+)', mode_text)
            bounce = re.search(r'return target .* to its owner', mode_text)
            
            if dmg:
                amount = int(dmg.group(1))
                mode['effect'] = self._make_damage_effect(amount, 'any target')
                mode['is_burn'] = True
            elif draw:
                count_str = draw.group(1)
                count_map = {'a': 1, 'two': 2, 'three': 3}
                count = count_map.get(count_str, None)
                if count is None:
                    try: count = int(count_str)
                    except ValueError: count = 1
                mode['effect'] = self._make_draw_effect(count)
            elif life:
                amount = int(life.group(1))
                mode['effect'] = self._make_lifegain_effect(amount)
            elif destroy:
                target_type = destroy.group(1)
                mode['effect'] = self._make_destroy_effect(target_type)
                mode['is_removal'] = True
            elif buff:
                p = int(buff.group(1))
                t = int(buff.group(2))
                mode['effect'] = self._make_buff_effect(p, t)
            elif bounce:
                mode['effect'] = self._make_bounce_spell_effect()
                mode['is_removal'] = True
            
            if mode['effect']:
                modes.append(mode)
        
        self.modal_modes = modes

    def _parse_cascade(self, text: str):
        """Parse Cascade (Rule 702.84): exile from top until you find lower CMC, cast free."""
        if not getattr(self, 'has_cascade', False):
            return
        from engine.player import Player as _P
        spell_cmc = _P._parse_cmc(self.cost) if self.cost else 0
        
        def cascade_trigger(game: 'Game', card: 'Card'):
            """Cascade: exile until CMC < spell, cast for free."""
            controller = card.controller
            exiled = []
            found = None
            for _ in range(len(controller.library)):
                if not controller.library.cards:
                    break
                top = controller.library.cards.pop(0)
                from engine.player import Player as _P2
                top_cmc = _P2._parse_cmc(top.cost) if top.cost else 0
                if not top.is_land and top_cmc < spell_cmc and top_cmc > 0:
                    found = top
                    break
                else:
                    exiled.append(top)
            
            if found:
                # Cast the found card for free
                found.controller = controller
                if found.is_creature:
                    found.summoning_sickness = not found.has_haste
                    found.tapped = False
                    found.damage_taken = 0
                    game.battlefield.add(found)
                    if found.etb_effect:
                        from engine.game import StackItem
                        trigger = StackItem(effect=found.etb_effect, source=found,
                                          controller=controller, description=f"ETB: {found.name}")
                        game.stack.cards.append(trigger)
                    game.log_event(f"  Cascade → {found.name} (free)")
                elif found.effect:
                    found.effect(game, found)
                    controller.graveyard.add(found)
                    game.log_event(f"  Cascade → {found.name} (free)")
                else:
                    game.battlefield.add(found)
                    game.log_event(f"  Cascade → {found.name} (free)")
            
            # Put exiled cards on bottom in random order
            random.shuffle(exiled)
            controller.library.cards.extend(exiled)
        
        # Store as a cast trigger (fires when this card is cast)
        self.cascade_trigger = cascade_trigger

    def _parse_delve(self, text: str):
        """Parse Delve (Rule 702.65): exile cards from GY to reduce generic cost."""
        if not getattr(self, 'has_delve', False):
            return
        from engine.player import Player as _P
        self.delve_base_cmc = _P._parse_cmc(self.cost) if self.cost else 0

    def _parse_affinity(self, text: str):
        """Parse Affinity for artifacts (Rule 702.40): reduce cost by artifact count."""
        if not getattr(self, 'has_affinity', False):
            return
        # Most cards say "Affinity for artifacts"
        if 'artifact' in text:
            self.affinity_type = 'artifacts'

    def _parse_annihilator(self, text: str):
        """Parse Annihilator N (Rule 702.85): defender sacrifices N permanents on attack."""
        if not getattr(self, 'has_annihilator', False):
            return
        ann_match = re.search(r'annihilator (\d+)', text)
        if ann_match:
            n = int(ann_match.group(1))
            self.annihilator_count = n
            
            def annihilator_trigger(game: 'Game', card: 'Card'):
                opp = game.defending_player
                opp_perms = [c for c in game.battlefield.cards if c.controller == opp]
                # Sacrifice N permanents (weakest first)
                opp_perms.sort(key=lambda c: (c.power or 0) + (c.toughness or 0))
                for i in range(min(n, len(opp_perms))):
                    sac = opp_perms[i]
                    game.battlefield.remove(sac)
                    opp.graveyard.add(sac)
                    game._fire_death_trigger(sac)
                    game.log_event(f"  Annihilator: {opp.name} sacrifices {sac.name}")
            
            # Override attack trigger
            existing_atk = self.attack_trigger
            if existing_atk:
                def combined_atk(game, card, old=existing_atk, ann=annihilator_trigger):
                    old(game, card)
                    ann(game, card)
                self.attack_trigger = combined_atk
            else:
                self.attack_trigger = annihilator_trigger

    def _parse_dredge(self, text: str):
        """Parse Dredge N (Rule 702.51): when you draw, you may mill N and return this instead."""
        if not getattr(self, 'has_dredge', False):
            return
        dredge_match = re.search(r'dredge (\d+)', text)
        if dredge_match:
            self.dredge_count = int(dredge_match.group(1))

    def _parse_unblockable(self, text: str):
        """Parse unblockable creatures ("can't be blocked")."""
        if re.search(r"can't be blocked\b", text) or re.search(r"is unblockable", text):
            self.is_unblockable = True

    def _parse_madness(self, text: str):
        """Parse Madness {cost} (Rule 702.34): cast for alternate cost when discarded."""
        madness_match = re.search(r'madness\s+(\{[^}]+\}(?:\{[^}]+\})*)', text)
        if madness_match:
            self.madness_cost = madness_match.group(1)
        elif 'madness' in text:
            # Handle "madness {0}" or "madness {1}{R}" etc.
            madness_match2 = re.search(r'madness\s+(\{[^\}]+\})', self.oracle_text.lower())
            if madness_match2:
                self.madness_cost = madness_match2.group(1)

    def _parse_convoke_mechanic(self, text: str):
        """Parse Convoke (Rule 702.50): tap creatures to help pay costs."""
        # has_convoke is already set by keyword detection
        # This method ensures the flag is properly available for cost reduction
        pass

    def _parse_emerge(self, text: str):
        """Parse Emerge {cost} (Rule 702.118): sacrifice creature to reduce cost."""
        emerge_match = re.search(r'emerge\s+(\{[^}]+\}(?:\{[^}]+\})*)', text)
        if emerge_match:
            self.emerge_cost = emerge_match.group(1)
        elif 'emerge' in text:
            emerge_match2 = re.search(r'emerge\s+(\{[^\}]+\})', self.oracle_text.lower())
            if emerge_match2:
                self.emerge_cost = emerge_match2.group(1)

    def _parse_toxic_count(self, text: str):
        """Parse Toxic N (Rule 702.164): deal N poison counters on combat damage."""
        toxic_match = re.search(r'toxic (\d+)', text)
        if toxic_match:
            self.has_toxic = True
            self.toxic_count = int(toxic_match.group(1))

    def _parse_bushido_count(self, text: str):
        """Parse Bushido N (Rule 702.44): +N/+N when blocking or blocked."""
        bushido_match = re.search(r'bushido (\d+)', text)
        if bushido_match:
            self.has_bushido = True
            self.bushido_count = int(bushido_match.group(1))

    def _parse_surveil(self, text: str):
        """Parse Surveil N: look at top N cards, put any into graveyard."""
        surveil_match = re.search(r'surveil (\d+)', text)
        if surveil_match:
            self.surveil_amount = int(surveil_match.group(1))

    def _parse_investigate_keyword(self, text: str):
        """Parse Investigate: create a Clue artifact token."""
        if re.search(r'\binvestigate\b', text):
            self.is_investigate = True

    def _parse_explore_keyword(self, text: str):
        """Parse Explore: reveal top, if land → hand, else +1/+1 and may discard."""
        if re.search(r'\bexplores?\b', text) and 'creature' not in text.split('explore')[0][-20:]:
            self.is_explore = True

    def _parse_connive_keyword(self, text: str):
        """Parse Connive: draw, discard, if nonland discarded → +1/+1."""
        if re.search(r'\bconnives?\b', text):
            self.is_connive = True

    def _parse_amass_keyword(self, text: str):
        """Parse Amass N [type]: create/grow a token army."""
        amass_match = re.search(r'amass (\w+)?\s*(\d+)', text)
        if amass_match:
            self.amass_type = amass_match.group(1) or 'zombies'
            self.amass_count = int(amass_match.group(2))
        elif re.search(r'amass (\d+)', text):
            m = re.search(r'amass (\d+)', text)
            self.amass_count = int(m.group(1))
            self.amass_type = 'zombies'

    def _parse_evoke(self, text: str):
        """Parse Evoke {cost} (Rule 702.73): cheap cast, sacrifice on ETB."""
        evoke_match = re.search(r'evoke\s+(\{[^}]+\}(?:\{[^}]+\})*)', text)
        if evoke_match:
            self.evoke_cost = evoke_match.group(1).upper()

    def _parse_unearth(self, text: str):
        """Parse Unearth {cost} (Rule 702.83): reanimate from GY, exile EoT."""
        unearth_match = re.search(r'unearth\s+(\{[^}]+\}(?:\{[^}]+\})*)', text)
        if unearth_match:
            self.unearth_cost = unearth_match.group(1).upper()

    def _parse_enters_tapped(self, text: str):
        """Parse 'enters the battlefield tapped' (Rule 305.7)."""
        if 'enters the battlefield tapped' in text or 'enters tapped' in text:
            self.enters_tapped = True

    def _parse_land_abilities(self, text: str):
        """Parse dual/shock/pain/fetch land mana production from Oracle text."""
        if not self.is_land:
            return
        
        colors_found = []
        mana_symbol_map = {
            'white': 'W', 'blue': 'U', 'black': 'B', 'red': 'R', 'green': 'G',
            '{w}': 'W', '{u}': 'U', '{b}': 'B', '{r}': 'R', '{g}': 'G'
        }
        
        # Detect mana production patterns: "{T}: Add {W} or {U}"
        add_match = re.findall(r'add \{([WUBRG])\}', text, re.IGNORECASE)
        if add_match:
            for c in add_match:
                if c.upper() not in colors_found:
                    colors_found.append(c.upper())
        
        # "Add one mana of any color" 
        if 'any color' in text:
            colors_found = ['W', 'U', 'B', 'R', 'G']
        
        # Also check type_line for basic land subtypes (e.g., "Land — Plains Island")
        land_type_colors = {
            'Plains': 'W', 'Island': 'U', 'Swamp': 'B', 'Mountain': 'R', 'Forest': 'G'
        }
        for lt, c in land_type_colors.items():
            if lt in self.type_line and c not in colors_found:
                colors_found.append(c)
        
        if colors_found:
            self.produced_mana = colors_found
        
        # Shock land: "pay 2 life" or enters tapped
        if re.search(r'pay 2 life', text) and re.search(r'enters .* tapped', text):
            self.is_shock_land = True
            # Default enters tapped unless life paid (handled by agent choice)
            self.enters_tapped = True
        
        # Pain land: "{T}: Add {C}" + "{T}, Pay 1 life: Add {W} or {U}"
        if re.search(r'pay 1 life', text) and re.search(r'add \{c\}', text, re.IGNORECASE):
            self.is_pain_land = True
            if 'C' not in (self.produced_mana or []):
                if not hasattr(self, 'produced_mana') or not self.produced_mana:
                    self.produced_mana = []
                self.produced_mana.insert(0, 'C')
        
        # Fetchland: "sacrifice ~: search your library for a ... land"
        if re.search(r'sacrifice.*search your library for', text):
            self.is_fetchland = True
            # Determine what land types can be fetched
            fetch_types = []
            for lt in ['plains', 'island', 'swamp', 'mountain', 'forest']:
                if lt in text:
                    fetch_types.append(lt.capitalize())
            self.fetch_targets = fetch_types if fetch_types else ['Plains', 'Island', 'Swamp', 'Mountain', 'Forest']
            
            # Add the fetch activated ability
            def fetch_ability(game, card):
                # Using the player's library fetching logic automatically or do it here
                card.controller.life -= 1
                card.controller.graveyard.add(card)
                if card in game.battlefield.cards:
                    game.battlefield.remove(card)
                
                # Simple fetch logic for the AI (similar to how player.py evaluates it)
                valid_lands = [l for l in card.controller.library.cards if l.is_land and any(t in getattr(l, 'type_line', '') or t == l.name for t in card.fetch_targets)]
                if valid_lands:
                    # Prefer duals/shocks over basics
                    best_land = max(valid_lands, key=lambda l: len(getattr(l, 'produced_mana', [])))
                    card.controller.library.remove(best_land)
                    best_land.controller = card.controller
                    game.battlefield.add(best_land)
                    
                    if getattr(best_land, 'is_shock_land', False):
                        if card.controller.life > 5:  # AI rough heuristic
                            card.controller.life -= 2
                            best_land.tapped = False
                            best_land.enters_tapped = False
                            game.log_event(f"Ability: {card.controller.name} fetches {best_land.name} (shock, paid 2 life, total life={card.controller.life})")
                        else:
                            best_land.tapped = True
                            game.log_event(f"Ability: {card.controller.name} fetches {best_land.name} (enters tapped, declined shock)")
                    elif getattr(best_land, 'enters_tapped', False):
                        best_land.tapped = True
                        game.log_event(f"Ability: {card.controller.name} fetches {best_land.name} (enters tapped)")
                    else:
                        best_land.tapped = False
                        game.log_event(f"Ability: {card.controller.name} fetches {best_land.name}")
                    
                    card.controller.library.shuffle()
                else:
                    game.log_event(f"Ability: {card.controller.name} activates {card.name} but found no land")
            
            self.activated_abilities.append({
                'cost_tap': True, 'cost_mana': '', 'cost_sacrifice': True,
                'effect': fetch_ability, 'description': "Fetch a land"
            })

    def _parse_restrictions(self, text: str):
        """Parse attack/block restrictions from oracle text.
        Only matches self-referencing restrictions, not offensive spells."""
        name_lower = self.name.lower()
        # Match self-restrictions: "CARDNAME can't attack" or "can't attack unless" 
        # (at start of sentence or after ~), but NOT "target creature can't attack"
        if re.search(r"(?:" + re.escape(name_lower) + r"|^~|this creature)\s+can't attack", text):
            self._cant_attack = True
        elif re.search(r"^can't attack", text):  # starts with "can't attack"
            self._cant_attack = True
        elif 'defender' in text:  # Defender already handled, but belt-and-suspenders
            pass
        
        if re.search(r"(?:" + re.escape(name_lower) + r"|^~|this creature)\s+can't block", text):
            self._cant_block = True
        elif re.search(r"^can't block", text):
            self._cant_block = True

    def _parse_etb_sacrifice(self, text: str):
        """Parse ETB self-sacrifice drawbacks (Rule 614).
        Handles patterns like 'sacrifice ~ unless', 'sacrifice ~ when ~ enters',
        'sacrifice ~ at end of turn'."""
        name_lower = self.name.lower()
        # "when ~ enters the battlefield, sacrifice it unless"
        if re.search(r'when .* enters.* sacrifice (?:it|~|' + re.escape(name_lower) + r')\b', text):
            self.has_drawback = True
            def sac_etb(game, card):
                # Simplified: always sacrifice (AI can't pay the "unless" cost)
                if card in game.battlefield.cards:
                    game.battlefield.remove(card)
                    card.controller.graveyard.add(card)
                    game.log_event(f"ETB drawback: {card.name} is sacrificed")
                    game._fire_death_trigger(card)
            # Chain with existing ETB if present
            existing_etb = self.etb_effect
            if existing_etb:
                def combined_etb(game, card, old=existing_etb, sac=sac_etb):
                    old(game, card)
                    sac(game, card)
                self.etb_effect = combined_etb
            else:
                self.etb_effect = sac_etb
            return
        # "sacrifice ~ at the beginning of the next end step" / "at end of turn"
        if re.search(r'sacrifice (?:it|~|' + re.escape(name_lower) + r') at', text):
            self.has_drawback = True
            return

    def _parse_x_cost(self):
        """Detect {X} in mana cost."""
        if '{X}' in self.cost.upper():
            self.has_x_cost = True

    def _parse_protection(self, text: str):
        """Parse 'protection from [color/type]' (Rule 702.16)."""
        prot_match = re.findall(r'protection from (\w+)', text)
        for p in prot_match:
            if p in ('white', 'blue', 'black', 'red', 'green',
                     'creatures', 'instants', 'sorceries', 'everything'):
                self.has_protection_from.append(p)

    def _parse_ward(self, text: str):
        """Parse 'ward {N}' (Rule 702.21)."""
        ward_match = re.search(r'ward \{(\d+)\}', text)
        if ward_match:
            self.has_ward = True
            self.ward_cost = f"{{{ward_match.group(1)}}}"
        elif 'ward' in text.split():
            # Ward with non-mana cost (e.g. "ward—discard a card")
            self.has_ward = True
            self.ward_cost = "{2}"  # Default fallback

    def _parse_counters_etb(self, text: str):
        """Parse 'enters the battlefield with N +1/+1 or -1/-1 counters' (Rule 122)."""
        # Match "with N +1/+1 counter(s)"
        counter_match = re.search(r'enters (?:the battlefield )?with (\d+) \+1/\+1 counter', text)
        if counter_match:
            count = int(counter_match.group(1))
            self.counters['+1/+1'] = count
        # Match "with a +1/+1 counter" (singular)
        elif re.search(r'enters (?:the battlefield )?with (?:a|an) \+1/\+1 counter', text):
            self.counters['+1/+1'] = 1
        
        # Match "with N -1/-1 counter(s)" (e.g. Moonshadow, Geralf's Messenger)
        minus_match = re.search(r'enters (?:the battlefield )?with (\d+) -1/-1 counter', text)
        if minus_match:
            count = int(minus_match.group(1))
            self.counters['-1/-1'] = count
            self.has_drawback = True
        # Match "with a -1/-1 counter" (singular)
        elif re.search(r'enters (?:the battlefield )?with (?:a|an) -1/-1 counter', text):
            self.counters['-1/-1'] = 1
            self.has_drawback = True

    def _parse_upkeep_trigger(self, text: str):
        """Parse 'at the beginning of your upkeep' triggers (Rule 503.1)."""
        if not re.search(r'at the beginning of (?:your |each )upkeep', text):
            return
        
        # "draw a card"
        if 'draw a card' in text or 'draw 1 card' in text:
            def upkeep_draw(game, card):
                card.controller.draw_card(1)
                game.log_event(f"Upkeep: {card.name} — {card.controller.name} draws a card")
            self.upkeep_effect = upkeep_draw
            return
        
        # "lose N life"
        life_loss = re.search(r'(?:you )?lose (\d+) life', text)
        if life_loss:
            amount = int(life_loss.group(1))
            def upkeep_life_loss(game, card, amt=amount):
                card.controller.life -= amt
                game.log_event(f"Upkeep: {card.name} — {card.controller.name} loses {amt} life ({card.controller.life})")
            self.upkeep_effect = upkeep_life_loss
            self.has_drawback = True
            return

        # "gain N life"
        life_gain = re.search(r'(?:you )?gain (\d+) life', text)
        if life_gain:
            amount = int(life_gain.group(1))
            def upkeep_life_gain(game, card, amt=amount):
                card.controller.life += amt
                game.log_event(f"Upkeep: {card.name} — {card.controller.name} gains {amt} life ({card.controller.life})")
            self.upkeep_effect = upkeep_life_gain
            return

        # "deals N damage to each opponent"
        upkeep_dmg = re.search(r'deals? (\d+) damage to each opponent', text)
        if upkeep_dmg:
            amount = int(upkeep_dmg.group(1))
            def upkeep_damage(game, card, amt=amount):
                opp = game.players[(game.players.index(card.controller) + 1) % 2]
                opp.life -= amt
                game.log_event(f"Upkeep: {card.name} deals {amt} to {opp.name} ({opp.life})")
            self.upkeep_effect = upkeep_damage
            return

        # "put a +1/+1 counter on ~"
        if re.search(r'put a \+1/\+1 counter on', text):
            def upkeep_counter(game, card):
                card.counters['+1/+1'] = card.counters.get('+1/+1', 0) + 1
                game.log_event(f"Upkeep: {card.name} gets a +1/+1 counter ({card.power}/{card.toughness})")
            self.upkeep_effect = upkeep_counter
            return

    def _parse_death_trigger(self, text: str):
        """Parse 'when ~ dies' triggers (Rule 700.4)."""
        if not re.search(r'when .* dies', text):
            return
        
        # "draw a card" / "draw N cards"
        death_draw_m = re.search(r'when .* dies.* draw (\d+|a|two|three) card', text)
        if death_draw_m or 'draw a card' in text:
            d = death_draw_m.group(1) if death_draw_m else 'a'
            n = {'a': 1, 'two': 2, 'three': 3}.get(d, None)
            if n is None:
                try: n = int(d)
                except ValueError: n = 1
            def death_draw(game, card, count=n):
                card.controller.draw_card(count, game=game)
                game.log_event(f"Death: {card.name} — {card.controller.name} draws {count}")
            self.death_effect = death_draw
            return
        
        # "deals N damage to"
        death_dmg = re.search(r'when .* dies.* deals? (\d+) damage', text)
        if death_dmg:
            amount = int(death_dmg.group(1))
            def death_damage(game, card, amt=amount):
                opp = game.players[(game.players.index(card.controller) + 1) % 2]
                opp.life -= amt
                game.log_event(f"Death: {card.name} deals {amt} to {opp.name} ({opp.life})")
            self.death_effect = death_damage
            return
        
        # "each opponent loses N life"
        death_life = re.search(r'when .* dies.* (?:each opponent )?loses? (\d+) life', text)
        if death_life:
            amount = int(death_life.group(1))
            def death_life_loss(game, card, amt=amount):
                opp = game.players[(game.players.index(card.controller) + 1) % 2]
                opp.life -= amt
                game.log_event(f"Death: {card.name} — {opp.name} loses {amt} life ({opp.life})")
            self.death_effect = death_life_loss
            return

        # "gain N life"
        death_gain = re.search(r'when .* dies.* gain (\d+) life', text)
        if death_gain:
            amount = int(death_gain.group(1))
            def death_gain_life(game, card, amt=amount):
                card.controller.life += amt
                game.log_event(f"Death: {card.name} — {card.controller.name} gains {amt} life ({card.controller.life})")
            self.death_effect = death_gain_life
            return

        # "create a N/N token"
        if re.search(r'when .* dies.*create', text):
            tok_match = re.search(r'create (?:a |an? )?(\d+)/(\d+)', text)
            if tok_match:
                tp, tt = int(tok_match.group(1)), int(tok_match.group(2))
                def death_token(game, card, p=tp, t=tt):
                    token = Card(name=f"{card.name} Token", cost="", type_line="Creature — Token",
                                 base_power=p, base_toughness=t)
                    token.controller = card.controller
                    token.summoning_sickness = True
                    game.battlefield.add(token)
                    game.log_event(f"Death: {card.name} creates a {p}/{t} token")
                self.death_effect = death_token
                return
        
        # "return target/a card from graveyard to hand/battlefield"
        if re.search(r'when .* dies.* return', text):
            def death_return(game, card):
                player = card.controller
                creatures = [c for c in player.graveyard.cards if c.is_creature and c != card]
                if creatures:
                    best = max(creatures, key=lambda c: (c.power or 0) + (c.toughness or 0))
                    player.graveyard.remove(best)
                    player.hand.add(best)
                    game.log_event(f"Death: {card.name} — {player.name} returns {best.name} to hand")
            self.death_effect = death_return
            return
        
        # "exile target creature"
        if re.search(r'when .* dies.* exile', text):
            def death_exile(game, card):
                opp = game.players[(game.players.index(card.controller) + 1) % 2]
                targets = [c for c in game.battlefield.cards if c.controller == opp and c.is_creature
                          and not c.has_hexproof]
                if targets:
                    t = max(targets, key=lambda c: (c.power or 0))
                    game.battlefield.remove(t)
                    opp.exile.add(t)
                    game.log_event(f"Death: {card.name} exiles {t.name}")
            self.death_effect = death_exile
            self.is_removal = True
            return
        
        # "each opponent sacrifices"
        if re.search(r'when .* dies.* sacrifices?', text):
            def death_sac(game, card):
                opp = game.players[(game.players.index(card.controller) + 1) % 2]
                opp_creatures = [c for c in game.battlefield.cards if c.controller == opp and c.is_creature]
                if opp_creatures:
                    weakest = min(opp_creatures, key=lambda c: (c.power or 0))
                    game.battlefield.remove(weakest)
                    if not weakest.is_token: opp.graveyard.add(weakest)
                    game.log_event(f"Death: {card.name} — {opp.name} sacrifices {weakest.name}")
            self.death_effect = death_sac
            self.is_removal = True
            return
        
        # "put a +1/+1 counter" / "-1/-1 counter"
        if re.search(r'when .* dies.*\+1/\+1 counter', text):
            def death_counter(game, card):
                my_creatures = [c for c in game.battlefield.cards 
                               if c.controller == card.controller and c.is_creature]
                if my_creatures:
                    best = max(my_creatures, key=lambda c: (c.power or 0))
                    best.counters['+1/+1'] = best.counters.get('+1/+1', 0) + 1
                    game.log_event(f"Death: {card.name} puts +1/+1 counter on {best.name}")
            self.death_effect = death_counter
            return
        
        # "mill N" on death
        death_mill = re.search(r'when .* dies.* mill (\d+)', text)
        if death_mill:
            amount = int(death_mill.group(1))
            def death_mill_eff(game, card, amt=amount):
                opp = game.players[(game.players.index(card.controller) + 1) % 2]
                for _ in range(amt):
                    if opp.library.cards:
                        milled = opp.library.draw()
                        if milled: opp.graveyard.add(milled)
                game.log_event(f"Death: {card.name} — {opp.name} mills {amt}")
            self.death_effect = death_mill_eff
            return
    
    def _parse_static_effect(self, text: str):
        """Parse static/continuous effects like anthems (Rule 613).
        
        IMPORTANT: Tribal/type-specific patterns must be checked BEFORE 
        generic 'creatures you control' patterns to avoid false matches.
        E.g. 'Other Merfolk creatures you control get +1/+1' would otherwise
        match 'creatures you control get +1/+1' first.
        """
        # Tribal: "other [Type](s) (creatures) (you control) get +N/+N"
        # Matches: "Other Merfolk get +1/+1", "Other Merfolk creatures you control get +1/+1"
        tribal = re.search(r'other (\w+?)s?\s+(?:creatures?\s+)?(?:you control\s+)?get \+(\d+)/\+(\d+)', text)
        if tribal:
            creature_type = tribal.group(1).capitalize()
            # Make sure it's actually a creature type, not "creatures" itself
            if creature_type.lower() not in ('creature', 'permanent', 'nontoken'):
                p_buff = int(tribal.group(2))
                t_buff = int(tribal.group(3))
                self.static_effect = {'power': p_buff, 'toughness': t_buff, 'filter': 'type', 'type': creature_type}
                return
        
        # "other creatures you control get +N/+N"
        anthem = re.search(r'other creatures you control get \+(\d+)/\+(\d+)', text)
        if anthem:
            p_buff = int(anthem.group(1))
            t_buff = int(anthem.group(2))
            self.static_effect = {'power': p_buff, 'toughness': t_buff, 'filter': 'other_creatures'}
            return
        
        # "creatures you control get +N/+N"
        anthem2 = re.search(r'creatures you control get \+(\d+)/\+(\d+)', text)
        if anthem2:
            p_buff = int(anthem2.group(1))
            t_buff = int(anthem2.group(2))
            self.static_effect = {'power': p_buff, 'toughness': t_buff, 'filter': 'all_creatures'}
            return
        
        # "creatures you control have" keyword — treat as +1/+1 anthem equivalent
        if re.search(r'creatures you control have (?:flying|trample|lifelink|deathtouch|vigilance|haste|menace|first strike|hexproof|reach)', text):
            self.static_effect = {'power': 1, 'toughness': 1, 'filter': 'all_creatures'}
            self.is_buff = True
            return
        
        # "creatures your opponents control get -N/-N"
        opp_debuff = re.search(r'creatures (?:your opponents?|each opponent) controls? get -(\d+)/-(\d+)', text)
        if opp_debuff:
            self.static_effect = {'power': int(opp_debuff.group(1)), 'toughness': int(opp_debuff.group(2)), 'filter': 'opponent_creatures'}
            self.is_removal = True
            return
        
        # "each creature gets -N/-N" (board-wide debuff)
        all_debuff = re.search(r'(?:each|all) creatures? gets? -(\d+)/-(\d+)', text)
        if all_debuff:
            self.static_effect = {'power': int(all_debuff.group(1)), 'toughness': int(all_debuff.group(2)), 'filter': 'all_debuff'}
            self.is_removal = True
            return
    
    def _parse_cda(self, text: str):
        """Parse Characteristic-Defining Abilities (CR 604.3 / Layer 7a).
        
        CDAs define a creature's P/T based on game state. They apply in
        Layer 7a, before any other P/T modifications.
        """
        if not self.is_creature:
            return
            
        # Death's Shadow: "gets -X/-X, where X is your life total"
        if 'gets -x/-x' in text and 'life total' in text:
            self.cda_type = 'deaths_shadow'
            return
        
        # Tarmogoyf: "power is equal to the number of card types among cards in all graveyards"
        if 'card types among cards in all graveyards' in text:
            self.cda_type = 'tarmogoyf'
            return
        
        # Scourge of the Skyclaves: "equal to 20 minus the highest life total"
        if '20 minus the highest life total' in text:
            self.cda_type = 'scourge_skyclaves'
            return
        
        # Serra Avatar: "power and toughness are each equal to your life total"
        if 'equal to your life total' in text:
            self.cda_type = 'serra_avatar'
            return
        
        # Nighthowler / Lord of Extinction: "creature cards in all graveyards"
        if 'creature cards in all graveyards' in text and ('equal to' in text or 'gets +x/+x' in text):
            self.cda_type = 'nighthowler'
            return
    
    def _parse_activated_abilities(self, text: str):
        """Parse activated abilities: '{T}: effect' and 'sacrifice ~: effect' patterns."""
        if not self.is_creature and not self.is_artifact and not self.is_enchantment:
            return
        
        # "{T}: deal N damage"
        tap_dmg = re.search(r'\{t\}:? .*deals? (\d+) damage', text)
        if tap_dmg:
            amount = int(tap_dmg.group(1))
            def tap_damage(game, card, amt=amount):
                opp = game.players[(game.players.index(card.controller) + 1) % 2]
                opp.life -= amt
                game.log_event(f"Ability: {card.name} deals {amt} to {opp.name} ({opp.life})")
            self.activated_abilities.append({
                'cost_tap': True, 'cost_mana': '', 'cost_sacrifice': False,
                'effect': tap_damage, 'description': f"Deal {amount} damage"
            })
        
        # "{T}: draw a card"
        if re.search(r'\{t\}:? .*draw (?:a )?card', text):
            def tap_draw(game, card):
                card.controller.draw_card(1)
                game.log_event(f"Ability: {card.name} — {card.controller.name} draws a card")
            self.activated_abilities.append({
                'cost_tap': True, 'cost_mana': '', 'cost_sacrifice': False,
                'effect': tap_draw, 'description': "Draw a card"
            })
        
        # "{T}: gain N life"
        tap_life = re.search(r'\{t\}:? .*gain (\d+) life', text)
        if tap_life:
            amount = int(tap_life.group(1))
            def tap_gain(game, card, amt=amount):
                card.controller.life += amt
                game.log_event(f"Ability: {card.name} — {card.controller.name} gains {amt} life ({card.controller.life})")
            self.activated_abilities.append({
                'cost_tap': True, 'cost_mana': '', 'cost_sacrifice': False,
                'effect': tap_gain, 'description': f"Gain {amount} life"
            })

        # "{T}: add {C}" (mana ability — not using the stack)
        if re.search(r'\{t\}:? add \{[wubrgc]\}', text):
            mana_match = re.search(r'\{t\}:? add \{([wubrgc])\}', text)
            if mana_match:
                color = mana_match.group(1).upper()
                def tap_mana(game, card, clr=color):
                    card.controller.mana_pool[clr] = card.controller.mana_pool.get(clr, 0) + 1
                    game.log_event(f"Ability: {card.name} adds {{{clr}}}")
                self.activated_abilities.append({
                    'cost_tap': True, 'cost_mana': '', 'cost_sacrifice': False,
                    'effect': tap_mana, 'description': f"Add {{{color}}}",
                    'is_mana_ability': True
                })
        
        # "sacrifice ~: effect"
        sac_match = re.search(r'sacrifice .*:(.+)', text)
        if sac_match and 'sacrifice' in text.split(':')[0] if ':' in text else False:
            sac_text = sac_match.group(1).strip()
            
            # "sacrifice ~: draw a card"
            if 'draw' in sac_text:
                draw_n = re.search(r'draw (\d+)', sac_text)
                n = int(draw_n.group(1)) if draw_n else 1
                def sac_draw(game, card, amt=n):
                    card.controller.draw_card(amt)
                    game.log_event(f"Sacrifice: {card.name} — {card.controller.name} draws {amt}")
                self.activated_abilities.append({
                    'cost_tap': False, 'cost_mana': '', 'cost_sacrifice': True,
                    'effect': sac_draw, 'description': f"Sacrifice: Draw {n}"
                })
            
            # "sacrifice ~: deal N damage"
            sac_dmg = re.search(r'deals? (\d+) damage', sac_text)
            if sac_dmg:
                amount = int(sac_dmg.group(1))
                def sac_damage(game, card, amt=amount):
                    opp = game.players[(game.players.index(card.controller) + 1) % 2]
                    opp.life -= amt
                    game.log_event(f"Sacrifice: {card.name} deals {amt} to {opp.name} ({opp.life})")
                self.activated_abilities.append({
                    'cost_tap': False, 'cost_mana': '', 'cost_sacrifice': True,
                    'effect': sac_damage, 'description': f"Sacrifice: Deal {amount} damage"
                })

    def _parse_token_effect(self, text: str):
        """Parse token creation from spell effects."""
        # Skip if already handled as ETB or death trigger
        if self.etb_effect or self.death_effect:
            return
        
        # "create N X/Y creature tokens" or "create a X/Y creature token"
        tok = re.search(r'create (\d+|a|an|two|three|four) (\d+)/(\d+)(?: [\w\s]+)? creature tokens?', text)
        if tok:
            count_str = tok.group(1)
            count_map = {'a': 1, 'an': 1, 'two': 2, 'three': 3, 'four': 4}
            count = count_map.get(count_str, None)
            if count is None:
                try:
                    count = int(count_str)
                except ValueError:
                    count = 1
            tp, tt = int(tok.group(2)), int(tok.group(3))
            
            def make_tokens(game, card, n=count, p=tp, t=tt):
                for _ in range(n):
                    token = Card(name=f"Token ({p}/{t})", cost="", type_line="Creature — Token",
                                 base_power=p, base_toughness=t)
                    token.controller = card.controller
                    token.summoning_sickness = True
                    game.battlefield.add(token)
                game.log_event(f"{card.name} creates {n} {p}/{t} token(s)")
            
            if self.is_instant or self.is_sorcery:
                self.effect = make_tokens
            else:
                self.token_effect = make_tokens
            return

    def _parse_equipment(self, text: str):
        """Parse Equipment keywords and abilities."""
        if 'Equipment' not in self.type_line:
            return
        
        self.is_equipment = True
            
        # Parse Equip cost: "Equip {N}"
        equip_match = re.search(r'equip \{(\d+)\}', text)
        if equip_match:
            self.equip_cost = f"{{{equip_match.group(1)}}}"
        
        # Parse stat bonuses: "Equipped creature gets +N/+N"
        bonus_match = re.search(r'equipped creature gets \+(\d+)/\+(\d+)', text)
        if bonus_match:
            self.equip_bonus = {'power': int(bonus_match.group(1)), 'toughness': int(bonus_match.group(2))}
        
        # Parse keyword grants: "equipped creature has/gains flying"
        # Also handles "and has flying" after a P/T bonus
        keyword_names = ['flying', 'trample', 'deathtouch', 'lifelink', 'vigilance', 
                        'haste', 'menace', 'hexproof', 'indestructible', 'reach']
        for kw in keyword_names:
            if re.search(rf'equipped creature (?:has|gains)\s+{kw}|and (?:has|gains)\s+{kw}', text):
                if not self.equip_bonus:
                    self.equip_bonus = {'power': 0, 'toughness': 0}
                self.equip_bonus[kw] = True
        if re.search(r'(?:has|gains)\s+first strike', text):
            if not self.equip_bonus:
                self.equip_bonus = {'power': 0, 'toughness': 0}
            self.equip_bonus['first_strike'] = True
        if re.search(r'(?:has|gains)\s+double strike', text):
            if not self.equip_bonus:
                self.equip_bonus = {'power': 0, 'toughness': 0}
            self.equip_bonus['double_strike'] = True

    def _parse_aura(self, text: str):
        """Parse Aura keywords and abilities."""
        if 'Aura' not in self.type_line:
            return
        
        self.is_aura = True
        
        # Parse enchant target: "Enchant creature"
        enchant_match = re.search(r'enchant (\w+)', text)
        if enchant_match:
            self.enchant_target_type = enchant_match.group(1)
            
        # Parse positive stat bonuses: "Enchanted creature gets +N/+N"
        bonus_match = re.search(r'enchanted creature gets \+(\d+)/\+(\d+)', text)
        if bonus_match:
            self.equip_bonus = {'power': int(bonus_match.group(1)), 'toughness': int(bonus_match.group(2))}
            return
        
        # Parse negative stat: "Enchanted creature gets -N/-N"
        debuff_match = re.search(r'enchanted creature gets -(\d+)/-(\d+)', text)
        if debuff_match:
            self.equip_bonus = {'power': -int(debuff_match.group(1)), 'toughness': -int(debuff_match.group(2))}
            self.is_removal = True
            return
        
        # Parse partial debuff: "enchanted creature gets -N/-0" or "-0/-N"
        partial_debuff = re.search(r'enchanted creature gets ([+-]\d+)/([+-]\d+)', text)
        if partial_debuff:
            p = int(partial_debuff.group(1))
            t = int(partial_debuff.group(2))
            self.equip_bonus = {'power': p, 'toughness': t}
            if p < 0 or t < 0:
                self.is_removal = True
            return
        
        # "Enchanted creature can't attack or block" — pseudo-removal
        if re.search(r"enchanted creature can.t (?:attack|block)", text):
            self.equip_bonus = {'power': 0, 'toughness': 0}
            self.is_removal = True
            return
        
        # Keyword granting auras: "Enchanted creature has flying/trample/etc."
        keyword_grant = re.search(r'enchanted creature (?:has|gains) (\w+)', text)
        if keyword_grant:
            kw = keyword_grant.group(1).lower()
            # Give a small stat bonus to represent the keyword value
            kw_bonus = {'flying': 1, 'trample': 1, 'haste': 1, 'lifelink': 1, 
                       'deathtouch': 1, 'vigilance': 1, 'hexproof': 1, 'indestructible': 2,
                       'menace': 1, 'first': 1, 'double': 2, 'reach': 1}.get(kw, 0)
            if kw_bonus:
                self.equip_bonus = {'power': kw_bonus, 'toughness': kw_bonus}
                self.is_buff = True
                return
    
    # ─── PROPERTIES ───────────────────────────────────────────────
    
    def _calc_power_pre_switch(self) -> int:
        p = self.base_power
        # 7b: Setting P/T
        for m in self._temp_modifiers:
            if 'set_power' in m: p = m['set_power']
        for att in self.attachments:
            if 'set_power' in att.equip_bonus: p = att.equip_bonus['set_power']
        # 7c: Modifiers and Counters
        p += sum(m.get('power', 0) for m in self._temp_modifiers)
        p += sum(att.equip_bonus.get('power', 0) for att in self.attachments)
        p += self.counters.get('+1/+1', 0) - self.counters.get('-1/-1', 0)
        return p

    def _calc_toughness_pre_switch(self) -> int:
        t = self.base_toughness
        # 7b: Setting P/T
        for m in self._temp_modifiers:
            if 'set_toughness' in m: t = m['set_toughness']
        for att in self.attachments:
            if 'set_toughness' in att.equip_bonus: t = att.equip_bonus['set_toughness']
        # 7c: Modifiers and Counters
        t += sum(m.get('toughness', 0) for m in self._temp_modifiers)
        t += sum(att.equip_bonus.get('toughness', 0) for att in self.attachments)
        t += self.counters.get('+1/+1', 0) - self.counters.get('-1/-1', 0)
        return t

    @property
    def power(self) -> Optional[int]:
        """Effective power calculated using CR 613 Layer 7 sublayers."""
        if self.base_power is None:
            return None
            
        # 7d: Switching
        is_switched = sum(1 for m in self._temp_modifiers if m.get('switch_pt')) % 2 != 0
        if is_switched:
            return self._calc_toughness_pre_switch()
        return self._calc_power_pre_switch()
    
    @power.setter
    def power(self, value):
        """Setting power directly sets the base (Layer 7a preview)."""
        self.base_power = value
    
    @property
    def toughness(self) -> Optional[int]:
        """Effective toughness calculated using CR 613 Layer 7 sublayers."""
        if self.base_toughness is None:
            return None
            
        # 7d: Switching
        is_switched = sum(1 for m in self._temp_modifiers if m.get('switch_pt')) % 2 != 0
        if is_switched:
            return self._calc_power_pre_switch()
        return self._calc_toughness_pre_switch()
    
    @toughness.setter
    def toughness(self, value):
        self.base_toughness = value
    
    def clear_temp_modifiers(self):
        """Clear all 'until end of turn' effects (Rule 514.2).
        Preserves static-tagged modifiers (anthems) since those are recalculated."""
        self._temp_modifiers = [m for m in self._temp_modifiers if m.get('_static')]

    def __repr__(self):
        return f"{self.name}"

    def __deepcopy__(self, memo):
        """Ensure every deep copy gets a unique card ID.
        
        Without this, all copies share the same id, and _get_card_by_id
        returns the wrong card (e.g., library copy instead of battlefield),
        corrupting all state mutations (tapped, damage, counters, etc.).
        """
        cls = self.__class__
        result = cls.__new__(cls)
        memo[id(self)] = result
        for k, v in self.__dict__.items():
            if k == 'id':
                # Assign a fresh unique ID
                object.__setattr__(result, k, next(_card_id_counter))
            else:
                object.__setattr__(result, k, copy.deepcopy(v, memo))
        return result

    @property
    def is_land(self) -> bool:
        return "Land" in self.type_line

    @property
    def is_legendary(self) -> bool:
        return "Legendary" in self.type_line

    @property
    def is_creature(self) -> bool:
        return "Creature" in self.type_line

    @property
    def is_instant(self) -> bool:
        return "Instant" in self.type_line

    @property
    def is_sorcery(self) -> bool:
        return "Sorcery" in self.type_line
    
    @property
    def is_enchantment(self) -> bool:
        return "Enchantment" in self.type_line
    
    @property
    def is_artifact(self) -> bool:
        return "Artifact" in self.type_line
    
    @property
    def is_planeswalker(self) -> bool:
        return "Planeswalker" in self.type_line
    
    @property
    def is_token(self) -> bool:
        return "Token" in self.type_line
    
    @property
    def can_block_flyer(self) -> bool:
        return self.has_flying or self.has_reach
    
    @property
    def can_attack(self) -> bool:
        return self.is_creature and not self.has_defender and not self._cant_attack
    
    @property
    def can_block(self) -> bool:
        """Whether this creature can be assigned as a blocker (Rule 509.1a)."""
        return self.is_creature and not self._cant_block

    def is_protected_from(self, source_card: 'Card') -> bool:
        """Check if this card has protection from the source (Rule 702.16)."""
        if not self.has_protection_from:
            return False
        if 'everything' in self.has_protection_from:
            return True
        # Check color-based protection
        color_map = {'W': 'white', 'U': 'blue', 'B': 'black', 'R': 'red', 'G': 'green'}
        source_colors = set()
        if hasattr(source_card, 'color_identity') and source_card.color_identity:
            for c in source_card.color_identity:
                if c in color_map:
                    source_colors.add(color_map[c])
        # Also infer from cost
        for c in re.findall(r'\{([WUBRG])\}', source_card.cost):
            if c in color_map:
                source_colors.add(color_map[c])
        
        for prot in self.has_protection_from:
            if prot in source_colors:
                return True
        return False

    # ─── EXISTING PARSERS (unchanged logic, fixed ETB damage) ────

    def parse_etb_effects(self):
        """Parse enters-the-battlefield effects from oracle text.
        Collects ALL matching effects (not just the first) and chains them."""
        text = self.oracle_text.lower()
        effects = []
        
        # Helper: match ETB context (old-style "enters the battlefield" OR modern "enters")
        ETB = r'(?:when|whenever) .* enters?(?:\s+the battlefield|\s+play)?'
        
        # ETB damage
        etb_dmg = re.search(ETB + r'.* deals? (\d+) damage', text)
        if not etb_dmg:
            etb_dmg = re.search(r'enters?(?:\s+the battlefield|\s+play).* deals? (\d+) damage', text)
        if etb_dmg:
            amount = int(etb_dmg.group(1))
            effects.append(self._make_etb_damage(amount))
            self.is_burn = True
        
        # ETB draw
        etb_draw = re.search(ETB + r'.* draw (\d+|a|two|three)', text)
        if not etb_draw:
            etb_draw = re.search(r'enters?(?:\s+the battlefield|\s+play).* draw (\d+|a|two|three)', text)
        if etb_draw:
            d = etb_draw.group(1)
            amount = {'a': 1, 'two': 2, 'three': 3}.get(d, None)
            if amount is None:
                try: amount = int(d)
                except ValueError: amount = 1
            effects.append(self._make_etb_draw(amount))
            self.is_draw = True
        
        # ETB destroy (creature, artifact, enchantment, permanent)
        if re.search(ETB + r'.* destroy target (?:creature|artifact|enchantment|permanent|nonland)', text):
            effects.append(self._make_etb_destroy())
            self.is_removal = True
        
        # ETB gain life
        etb_life = re.search(ETB + r'.* gain (\d+) life', text)
        if etb_life:
            amount = int(etb_life.group(1))
            effects.append(self._make_etb_lifegain(amount))
            self.is_lifegain = True
        
        # ETB bounce (broad: return any target to hand)
        if re.search(ETB + r'.* return target.* (?:creature|permanent|nonland|artifact)', text):
            effects.append(self._make_etb_bounce())
            self.is_removal = True
        
        # ETB exile target (creature, permanent, artifact, enchantment)
        if re.search(ETB + r'.* exile target.* (?:creature|permanent|artifact|enchantment|nonland)', text):
            effects.append(self._make_etb_exile())
            self.is_removal = True
        
        # ETB create token(s)
        etb_token = re.search(ETB + r'.* create (\d+|a|an|two|three|four|five) (\d+)/(\d+)', text)
        if not etb_token:
            etb_token = re.search(r'enters?(?:\s+the battlefield|\s+play).* create (\d+|a|an|two|three|four|five) (\d+)/(\d+)', text)
        if etb_token:
            count_str = etb_token.group(1)
            count_map = {'a': 1, 'an': 1, 'two': 2, 'three': 3, 'four': 4, 'five': 5}
            count = count_map.get(count_str, None) or int(count_str)
            p = int(etb_token.group(2))
            t = int(etb_token.group(3))
            effects.append(self._make_etb_create_token(count, p, t))
        
        # ETB put +1/+1 counter
        if re.search(ETB + r'.* put (?:a|\d+) \+1/\+1 counter', text):
            effects.append(self._make_etb_counter())
        
        # ETB mill
        etb_mill = re.search(ETB + r'.* (?:target )?(?:opponent|player) (?:mills?|puts?) .* (\d+)', text)
        if not etb_mill:
            etb_mill = re.search(ETB + r'.* mill (\d+)', text)
        if etb_mill:
            amount = int(etb_mill.group(1))
            effects.append(self._make_etb_mill(amount))
        
        # ETB discard
        if re.search(ETB + r'.* (?:target )?opponent discards?', text):
            effects.append(self._make_etb_discard())
            self.is_discard = True
        
        # ETB search library
        if re.search(ETB + r'.* search your library', text):
            effects.append(self._make_etb_search())
        
        # ETB tap target creature
        if re.search(ETB + r'.* tap target (?:creature|permanent)', text):
            def etb_tap_effect(game, card):
                opp = game.players[(game.players.index(card.controller) + 1) % 2]
                targets = [c for c in game.battlefield.cards if c.controller == opp and c.is_creature
                          and not c.tapped and not c.has_hexproof]
                if targets:
                    best = max(targets, key=lambda c: (c.power or 0))
                    best.tapped = True
                    game.log_event(f"ETB: {card.name} taps {best.name}")
            effects.append(etb_tap_effect)
        
        # ETB scry
        etb_scry = re.search(ETB + r'.* scry (\d+)', text)
        if etb_scry:
            n = int(etb_scry.group(1))
            def make_etb_scry(amount):
                def eff(game, card):
                    game.do_surveil(card.controller, amount)  # Surveil is a superset of scry
                return eff
            effects.append(make_etb_scry(n))
        
        # ETB sacrifice opponent permanent  
        if re.search(ETB + r'.* (?:target )?opponent sacrifices?', text):
            def etb_sac_effect(game, card):
                opp = game.players[(game.players.index(card.controller) + 1) % 2]
                opp_perms = [c for c in game.battlefield.cards if c.controller == opp]
                if opp_perms:
                    weakest = min(opp_perms, key=lambda c: (c.power or 0) + (c.toughness or 0))
                    game.battlefield.remove(weakest)
                    if weakest.is_token:
                        game.log_event(f"ETB: {card.name} — {opp.name} sacrifices {weakest.name} (token)")
                    else:
                        opp.graveyard.add(weakest)
                        game.log_event(f"ETB: {card.name} — {opp.name} sacrifices {weakest.name}")
            effects.append(etb_sac_effect)
            self.is_removal = True
        
        # Clone/Copy effect (Rule 707): "enters as a copy of"
        if re.search(r'(?:enters|enter) (?:the battlefield )?as a copy', text):
            effects.append(self._make_clone_etb())
        
        # === KEYWORD ACTION ETB EFFECTS ===
        
        # Surveil on ETB
        if self.surveil_amount > 0 and re.search(r'(?:when|whenever) .* enters|enters the battlefield', text):
            n = self.surveil_amount
            def make_surveil_etb(amount):
                def eff(game, card):
                    game.do_surveil(card.controller, amount)
                return eff
            effects.append(make_surveil_etb(n))
        
        # Investigate on ETB
        if self.is_investigate and re.search(r'(?:when|whenever) .* enters|enters the battlefield', text):
            def investigate_etb(game, card):
                game.do_investigate(card.controller)
            effects.append(investigate_etb)
        
        # Explore on ETB (very common: "When ~ enters, it explores")
        if self.is_explore and self.is_creature:
            def explore_etb(game, card):
                game.do_explore(card)
            effects.append(explore_etb)
        
        # Connive on ETB
        if self.is_connive and re.search(r'(?:when|whenever) .* enters|enters the battlefield', text):
            def connive_etb(game, card):
                game.do_connive(card)
            effects.append(connive_etb)
        
        # Amass on ETB
        if self.amass_count > 0 and re.search(r'(?:when|whenever) .* enters|enters the battlefield', text):
            n = self.amass_count
            atype = self.amass_type or 'Zombies'
            def make_amass_etb(amount, army_type):
                def eff(game, card):
                    game.do_amass(card.controller, amount, army_type)
                return eff
            effects.append(make_amass_etb(n, atype))
        
        # Rest in Peace ETB: "exile all graveyards"
        if self.name == 'Rest in Peace':
            def rip_etb(game, card):
                for p in game.players:
                    for c in list(p.graveyard.cards):
                        p.graveyard.remove(c)
                        game.exile.add(c)
                game.log_event(f"ETB: {card.name} exiles all graveyards")
            effects.append(rip_etb)
        
        # Evoke: sacrifice on ETB if cast for evoke cost (Rule 702.73)
        if self.evoke_cost:
            def evoke_sacrifice_etb(game, card):
                if getattr(card, '_was_evoked', False):
                    if card in game.battlefield.cards:
                        game.battlefield.remove(card)
                        card.controller.graveyard.add(card)
                        game._fire_death_trigger(card)
                        game.log_event(f"  Evoke: {card.name} is sacrificed")
            effects.append(evoke_sacrifice_etb)
        
        # Chain all effects into one
        if len(effects) == 1:
            self.etb_effect = effects[0]
        elif len(effects) > 1:
            def combined_etb(game, card, fns=effects):
                for fn in fns:
                    fn(game, card)
            self.etb_effect = combined_etb

    def _make_etb_damage(self, amount):
        """ETB damage now marks damage (Rule 120.6) instead of direct removal."""
        def effect(game, card):
            opp = game.players[(game.players.index(card.controller) + 1) % 2]
            # Try to damage the best creature we can kill
            targets = [c for c in game.battlefield.cards 
                      if c.controller == opp and c.is_creature 
                      and not c.has_hexproof and not c.is_protected_from(card)]
            killable = [t for t in targets if (t.toughness or 0) - t.damage_taken <= amount]
            if killable:
                killable.sort(key=lambda c: c.power or 0, reverse=True)
                t = killable[0]
                t.damage_taken += amount  # Mark damage, let SBAs handle death
                game.log_event(f"ETB: {card.name} deals {amount} damage to {t.name} (dmg={t.damage_taken}/{t.toughness})")
            elif targets:
                targets.sort(key=lambda c: c.power or 0, reverse=True)
                t = targets[0]
                t.damage_taken += amount
                game.log_event(f"ETB: {card.name} deals {amount} damage to {t.name} (dmg={t.damage_taken}/{t.toughness})")
            else:
                opp.life -= amount
                game.log_event(f"ETB: {card.name} deals {amount} to {opp.name} ({opp.life} life)")
        return effect
    
    def _make_etb_draw(self, amount):
        def effect(game, card):
            card.controller.draw_card(amount)
            game.log_event(f"ETB: {card.name} — {card.controller.name} draws {amount}")
        return effect
    
    def _make_etb_destroy(self):
        def effect(game, card):
            opp = game.players[(game.players.index(card.controller) + 1) % 2]
            targets = [c for c in game.battlefield.cards 
                      if c.controller == opp and c.is_creature 
                      and not c.has_hexproof and not c.is_protected_from(card)]
            if targets:
                targets.sort(key=lambda c: c.power or 0, reverse=True)
                t = targets[0]
                if not t.has_indestructible:
                    game.battlefield.remove(t); t.controller.graveyard.add(t)
                    game.log_event(f"ETB: {card.name} destroys {t.name}")
        return effect
    
    def _make_etb_lifegain(self, amount):
        def effect(game, card):
            card.controller.life += amount
            game.log_event(f"ETB: {card.name} — {card.controller.name} gains {amount} life ({card.controller.life})")
        return effect
    
    def _make_etb_bounce(self):
        def effect(game, card):
            opp = game.players[(game.players.index(card.controller) + 1) % 2]
            targets = [c for c in game.battlefield.cards 
                      if c.controller == opp and c.is_creature
                      and not c.is_protected_from(card)]
            if targets:
                targets.sort(key=lambda c: c.power or 0, reverse=True)
                t = targets[0]
                game.battlefield.remove(t)
                if t.is_token:
                    # Tokens cease to exist when they leave the battlefield (Rule 111.7)
                    game.log_event(f"ETB: {card.name} bounces {t.name} (token ceases to exist)")
                else:
                    opp.hand.add(t)
                    game.log_event(f"ETB: {card.name} bounces {t.name} to hand")
        return effect

    def _make_etb_exile(self):
        """ETB exile target creature (Fiend Hunter, Skyclave Apparition)."""
        def effect(game, card):
            opp = game.players[(game.players.index(card.controller) + 1) % 2]
            targets = [c for c in game.battlefield.cards 
                      if c.controller == opp and c.is_creature
                      and not c.has_hexproof and not c.is_protected_from(card)]
            if targets:
                targets.sort(key=lambda c: c.power or 0, reverse=True)
                t = targets[0]
                game.battlefield.remove(t)
                game.exile.add(t)
                game.log_event(f"ETB: {card.name} exiles {t.name}")
        return effect

    def _make_etb_create_token(self, count, power, toughness):
        """ETB create N creature tokens."""
        def effect(game, card):
            from engine.card import _card_id_counter
            for _ in range(count):
                token = Card(
                    name=f"Token ({power}/{toughness})",
                    cost="",
                    type_line="Token Creature",
                    base_power=power,
                    base_toughness=toughness
                )
                token.id = next(_card_id_counter)
                token.controller = card.controller
                token.summoning_sickness = True
                token.tapped = False
                token.damage_taken = 0
                game.battlefield.add(token)
            game.log_event(f"ETB: {card.name} creates {count} {power}/{toughness} token(s)")
        return effect

    def _make_etb_counter(self):
        """ETB put +1/+1 counter on target creature you control."""
        def effect(game, card):
            own = [c for c in game.battlefield.cards 
                  if c.controller == card.controller and c.is_creature and c != card]
            if own:
                own.sort(key=lambda c: c.power or 0, reverse=True)
                t = own[0]
                t.counters_p1p1 = getattr(t, 'counters_p1p1', 0) + 1
                game.log_event(f"ETB: {card.name} puts +1/+1 counter on {t.name}")
        return effect

    def _make_etb_mill(self, amount):
        """ETB mill opponent for N cards."""
        def effect(game, card):
            opp = game.players[(game.players.index(card.controller) + 1) % 2]
            milled = 0
            for _ in range(amount):
                if opp.library.cards:
                    top = opp.library.cards.pop(0)
                    opp.graveyard.add(top)
                    milled += 1
            if milled > 0:
                game.log_event(f"ETB: {card.name} mills {opp.name} for {milled}")
        return effect

    def _make_etb_discard(self):
        """ETB opponent discards a card."""
        def effect(game, card):
            opp = game.players[(game.players.index(card.controller) + 1) % 2]
            if opp.hand.cards:
                # Discard weakest card (simplified)
                opp.hand.cards.sort(key=lambda c: c.power or 0)
                discarded = opp.hand.cards.pop(0)
                opp.graveyard.add(discarded)
                game.log_event(f"ETB: {card.name} — {opp.name} discards {discarded.name}")
        return effect

    def _make_etb_search(self):
        """ETB search library for a land, put into hand (simplified — most creature ETB searches find lands)."""
        def effect(game, card):
            player = card.controller
            lands = [c for c in player.library.cards if c.is_land]
            if lands:
                chosen = lands[0]
                player.library.remove(chosen)
                player.hand.add(chosen)
                import random
                random.shuffle(player.library.cards)
                game.log_event(f"ETB: {card.name} — {player.name} searches for {chosen.name}")
        return effect

    def _make_clone_etb(self):
        """Clone/Copy (Rule 707): enter as a copy of target creature on battlefield."""
        def effect(game, card):
            # Find best creature to copy (excluding self)
            all_creatures = [c for c in game.battlefield.cards 
                           if c.is_creature and c != card and not c.is_token]
            if not all_creatures:
                game.log_event(f"ETB: {card.name} — no creature to copy")
                return
            # Copy the strongest creature
            all_creatures.sort(key=lambda c: (c.power or 0) + (c.toughness or 0), reverse=True)
            target = all_creatures[0]
            
            # Apply copy (Rule 707.2 — copies characteristics)
            card.name = f"{target.name} (Copy)"
            card.base_power = target.base_power
            card.base_toughness = target.base_toughness
            card.type_line = target.type_line
            card.creature_types = list(target.creature_types)
            # Copy keyword abilities
            for attr in ['has_flying', 'has_trample', 'has_lifelink', 'has_deathtouch',
                        'has_first_strike', 'has_double_strike', 'has_vigilance', 'has_haste',
                        'has_hexproof', 'has_menace', 'has_reach', 'has_defender',
                        'has_indestructible', 'has_flash', 'has_prowess']:
                setattr(card, attr, getattr(target, attr, False))
            # Copy abilities
            card.attack_trigger = getattr(target, 'attack_trigger', None)
            card.death_trigger = getattr(target, 'death_trigger', None)
            game.log_event(f"ETB: {card.name} copies {target.name} ({target.power}/{target.toughness})")
        return effect

    def parse_effects(self):
        """Parse spell effects from oracle text."""
        text = self.oracle_text.lower()
        
        # Damage (Burn) — handles both numeric and X-based damage
        dmg_match = re.search(r"deals (\d+|x) damage to (any target|target creature|target player|each opponent)", text)
        if dmg_match:
            amount_str = dmg_match.group(1)
            amount = 0 if amount_str == 'x' else int(amount_str)  # X=0 → resolved via card.x_value
            target_type = dmg_match.group(2)
            self.effect = self._make_damage_effect(amount, target_type)
            self.is_burn = True
            if "creature" in target_type: self.is_removal = True
            return

        # Destroy creature
        if "destroy target creature" in text:
            self.effect = self._make_destroy_effect("creature")
            self.is_removal = True
            return
        
        # Destroy artifact/enchantment
        if "destroy target artifact" in text or "destroy target enchantment" in text:
            self.effect = self._make_destroy_permanent_effect()
            return
        
        # Exile target creature
        if "exile target creature" in text:
            self.effect = self._make_exile_effect()
            self.is_removal = True
            return

        # Draw Cards
        draw_match = re.search(r"draw (\d+) cards?", text)
        if draw_match:
            amount = int(draw_match.group(1))
            self.effect = self._make_draw_effect(amount)
            self.is_draw = True
            return
        
        # Gain life
        life_match = re.search(r"(?:you )?gain (\d+) life", text)
        if life_match and not self.is_creature:
            amount = int(life_match.group(1))
            self.effect = self._make_lifegain_effect(amount)
            self.is_lifegain = True
            return
            
        # Counter Spell
        if "counter target spell" in text:
            self.effect = self._make_counter_effect()
            self.is_counter = True
            return
        
        # Board Wipe: "destroy all creatures" patterns (Rule 701.7)
        if re.search(r'destroy all creatures', text) or re.search(r'all creatures get \-\d+/\-\d+', text):
            self.effect = self._make_board_wipe_effect()
            self.is_board_wipe = True
            self.is_removal = True
            return

        # Buff: target creature gets +N/+N
        buff_match = re.search(r"target creature gets? \+(\d+)/\+(\d+)", text)
        if buff_match:
            p_buff = int(buff_match.group(1))
            t_buff = int(buff_match.group(2))
            self.effect = self._make_buff_effect(p_buff, t_buff)
            self.is_buff = True
            return

        # Debuff: target creature gets -N/-N until end of turn
        debuff_match = re.search(r"target creature gets? -(\d+)/-(\d+)", text)
        if debuff_match:
            p_debuff = int(debuff_match.group(1))
            t_debuff = int(debuff_match.group(2))
            self.effect = self._make_debuff_effect(p_debuff, t_debuff)
            self.is_removal = True
            return

        # Discard: target player/opponent discards
        discard_match = re.search(r'(?:target (?:player|opponent)|each opponent) discards? (?:(\d+|a|an|two|three) )?cards?', text)
        if discard_match:
            count_str = discard_match.group(1) if discard_match.group(1) else '1'
            count_map = {'a': 1, 'an': 1, 'two': 2, 'three': 3}
            count = count_map.get(count_str, None)
            if count is None:
                try:
                    count = int(count_str)
                except ValueError:
                    count = 1
            self.effect = self._make_discard_effect(count)
            self.is_discard = True
            return

        # Search library
        search_match = re.search(r'search your library for (?:a|an|up to \w+) (\w+) cards?', text)
        if search_match:
            card_type = search_match.group(1)
            self.effect = self._make_search_effect(card_type)
            return

        # Return from graveyard to hand
        if re.search(r'return target (?:creature )?card from your graveyard to your hand', text):
            self.effect = self._make_graveyard_return_effect('hand')
            return

        # Return from graveyard to battlefield
        if re.search(r'return target (?:creature )?card from your graveyard to the battlefield', text):
            self.effect = self._make_graveyard_return_effect('battlefield')
            return

        # --- Tier 6: Fight (Rule 701.12) ---
        if 'target creature you control fights' in text or 'fights target creature' in text:
            self.effect = self._make_fight_effect()
            self.is_fight = True
            self.is_removal = True
            return

        # --- Tier 6: Mill (Rule 701.13) ---
        mill_match = re.search(r'(?:target (?:player|opponent)|each opponent).*?mills? (\d+)', text)
        if not mill_match:
            mill_match = re.search(r'mill (\d+)', text)
        if mill_match:
            amount = int(mill_match.group(1))
            self.effect = self._make_mill_effect(amount)
            self.is_mill = True
            return
        # Also "put the top N cards ... into ... graveyard"
        mill_alt = re.search(r'put the top (\d+) cards? of .* library into .* graveyard', text)
        if mill_alt:
            amount = int(mill_alt.group(1))
            self.effect = self._make_mill_effect(amount)
            self.is_mill = True
            return

        # --- Tier 6: Proliferate (Rule 701.27) ---
        if 'proliferate' in text:
            self.effect = self._make_proliferate_effect()
            self.is_proliferate = True
            return

        # --- Tier 7: Bounce (Rule 701.4) ---
        if re.search(r'return target (?:creature|nonland permanent) to its owner', text):
            self.effect = self._make_bounce_spell_effect()
            self.is_bounce = True
            self.is_removal = True
            return

        # --- Tier 7: Sacrifice-a-creature (additional cost) ---
        if re.search(r'(?:as an additional cost|sacrifice a creature)', text):
            self.requires_creature_sacrifice = True  # Flag for get_legal_actions validation
            # Combine sacrifice-a-creature with whatever main effect follows
            dmg_match = re.search(r'deals? (\d+) damage', text)
            draw_match = re.search(r'draw (\d+|a|two|three) cards?', text)
            if dmg_match:
                amount = int(dmg_match.group(1))
                self.effect = self._make_sac_creature_damage_effect(amount)
                self.is_removal = True
                return
            elif draw_match:
                count_str = draw_match.group(1)
                count_map = {'a': 1, 'two': 2, 'three': 3}
                count = count_map.get(count_str, None)
                if count is None:
                    try: count = int(count_str)
                    except ValueError: count = 1
                self.effect = self._make_sac_creature_draw_effect(count)
                return

        # --- Tier 7: Create Treasure tokens ---
        treasure_match = re.search(r'create (\d+|a|an|two|three) treasure tokens?', text)
        if treasure_match:
            count_str = treasure_match.group(1)
            count_map = {'a': 1, 'an': 1, 'two': 2, 'three': 3}
            count = count_map.get(count_str, None)
            if count is None:
                try: count = int(count_str)
                except ValueError: count = 1
            self.effect = self._make_treasure_effect(count)
            return
        
        # === EXPANDED SPELL PARSERS (Tier 8) ===
        # These catch broader patterns missed by Tier 1-7 above
        
        # Broad draw: "draw a card", "draw cards", conditional draws
        broad_draw = re.search(r'draw (?:a |)(card|two|three|\d+)', text)
        if broad_draw and not self.effect:
            d = broad_draw.group(1)
            amount = {'a': 1, 'card': 1, 'two': 2, 'three': 3}.get(d, None)
            if amount is None:
                try: amount = int(d)
                except ValueError: amount = 1
            self.effect = self._make_draw_effect(amount)
            self.is_draw = True
            return
        
        # Broad damage: any "deals N damage" pattern not caught above
        broad_dmg = re.search(r'deals? (\d+) damage', text)
        if broad_dmg and not self.effect:
            amount = int(broad_dmg.group(1))
            self.effect = self._make_damage_effect(amount, 'any target')
            self.is_burn = True
            return
        
        # Exile target (non-creature: artifact, enchantment, permanent, nonland)
        if re.search(r'exile target (?:artifact|enchantment|permanent|nonland|nonland permanent)', text):
            self.effect = self._make_exile_effect()
            self.is_removal = True
            return
        
        # Mass exile: "exile all" patterns
        if re.search(r'exile all (?:creatures|permanents|nonland permanents|artifacts|enchantments)', text):
            self.effect = self._make_board_wipe_effect()
            self.is_board_wipe = True
            self.is_removal = True
            return
        
        # Mass destroy (non-creature): "destroy all artifacts/enchantments/permanents"
        if re.search(r'destroy all (?:artifacts?|enchantments?|permanents?|nonland permanents?)', text):
            self.effect = self._make_board_wipe_effect()
            self.is_board_wipe = True
            self.is_removal = True
            return
        
        # Destroy target (broad): artifact, enchantment, permanent, planeswalker
        if re.search(r'destroy target (?:artifact|enchantment|permanent|nonland permanent|planeswalker)', text):
            self.effect = self._make_destroy_permanent_effect()
            self.is_removal = True
            return
        
        # Broad bounce: return target to hand
        if re.search(r'return target (?:creature|permanent|nonland permanent|artifact|enchantment).* to (?:its|their) owner', text):
            self.effect = self._make_bounce_spell_effect()
            self.is_bounce = True
            self.is_removal = True
            return
        
        # Broad gain life: any "gain N life" for spells
        broad_life = re.search(r'gain (\d+) life', text)
        if broad_life and not self.effect and not self.is_creature:
            amount = int(broad_life.group(1))
            self.effect = self._make_lifegain_effect(amount)
            self.is_lifegain = True
            return
        
        # Put +1/+1 counters on target creature
        counter_put = re.search(r'put (?:a|one|two|three|(\d+)) \+1/\+1 counters? on target creature', text)
        if counter_put and not self.effect:
            ct = counter_put.group(1)
            amount = {'a': 1, 'one': 1, 'two': 2, 'three': 3}.get(ct, None) if not ct or not ct.isdigit() else int(ct)
            if amount is None: amount = 1
            def make_counter_effect(n):
                def eff(game, card):
                    opp = game.players[(game.players.index(card.controller) + 1) % 2]
                    my_creatures = [c for c in game.battlefield.cards 
                                   if c.controller == card.controller and c.is_creature]
                    if my_creatures:
                        best = max(my_creatures, key=lambda c: (c.power or 0))
                        best.counters['+1/+1'] = best.counters.get('+1/+1', 0) + n
                        game.log_event(f"{card.name}: puts {n} +1/+1 counters on {best.name}")
                return eff
            self.effect = make_counter_effect(amount)
            self.is_buff = True
            return
        
        # Put -1/-1 counters on target creature
        neg_counter = re.search(r'put (?:a|one|two|three|(\d+)) -1/-1 counters? on target creature', text)
        if neg_counter and not self.effect:
            ct = neg_counter.group(1)
            amount = {'a': 1, 'one': 1, 'two': 2, 'three': 3}.get(ct, None) if not ct or not ct.isdigit() else int(ct)
            if amount is None: amount = 1
            def make_neg_counter_effect(n):
                def eff(game, card):
                    opp = game.players[(game.players.index(card.controller) + 1) % 2]
                    targets = [c for c in game.battlefield.cards if c.controller == opp and c.is_creature
                              and not c.has_hexproof and not c.is_protected_from(card)]
                    if targets:
                        best = max(targets, key=lambda c: (c.power or 0))
                        best.counters['-1/-1'] = best.counters.get('-1/-1', 0) + n
                        game.log_event(f"{card.name}: puts {n} -1/-1 counters on {best.name}")
                return eff
            self.effect = make_neg_counter_effect(amount)
            self.is_removal = True
            return
        
        # Create generic tokens (not just treasure): "create N X/Y creature tokens"
        token_match = re.search(r'create (?:(a|an|one|two|three|four|five|\d+) )?(\d+)/(\d+)', text)
        if token_match and not self.effect:
            cnt_str = token_match.group(1) or '1'
            cnt_map = {'a': 1, 'an': 1, 'one': 1, 'two': 2, 'three': 3, 'four': 4, 'five': 5}
            cnt = cnt_map.get(cnt_str, None)
            if cnt is None:
                try: cnt = int(cnt_str)
                except ValueError: cnt = 1
            tp = int(token_match.group(2))
            tt = int(token_match.group(3))
            self.effect = self._make_etb_create_token(cnt, tp, tt)
            return
        
        # Tap target creature
        if re.search(r'tap target (?:creature|permanent)', text) and not self.effect:
            def tap_effect(game, card):
                opp = game.players[(game.players.index(card.controller) + 1) % 2]
                targets = [c for c in game.battlefield.cards if c.controller == opp and c.is_creature
                          and not c.tapped and not c.has_hexproof]
                if targets:
                    best = max(targets, key=lambda c: (c.power or 0))
                    best.tapped = True
                    game.log_event(f"{card.name}: taps {best.name}")
            self.effect = tap_effect
            return
        
        # Cantrip patterns (do something + draw a card)
        if 'draw a card' in text and not self.effect:
            self.effect = self._make_draw_effect(1)
            self.is_draw = True
            return
        
        # Creatures you control get +N/+N
        team_buff = re.search(r'creatures you control get \+(\d+)/\+(\d+)', text)
        if team_buff and not self.effect:
            p = int(team_buff.group(1))
            t = int(team_buff.group(2))
            def make_team_buff(pw, tw):
                def eff(game, card):
                    for c in game.battlefield.cards:
                        if c.controller == card.controller and c.is_creature:
                            if not hasattr(c, '_temp_modifiers'): c._temp_modifiers = []
                            c._temp_modifiers.append({'power': pw, 'toughness': tw})
                    game.log_event(f"{card.name}: creatures get +{pw}/+{tw}")
                return eff
            self.effect = make_team_buff(p, t)
            self.is_buff = True
            return
        
        # Gain control of target creature
        if re.search(r'gain control of target (?:creature|permanent)', text) and not self.effect:
            def steal_effect(game, card):
                opp = game.players[(game.players.index(card.controller) + 1) % 2]
                targets = [c for c in game.battlefield.cards if c.controller == opp and c.is_creature
                          and not c.has_hexproof and not c.is_protected_from(card)]
                if targets:
                    best = max(targets, key=lambda c: (c.power or 0))
                    best.controller = card.controller
                    game.log_event(f"{card.name}: {card.controller.name} steals {best.name}")
            self.effect = steal_effect
            self.is_removal = True
            return
        
        # Return from graveyard (broad patterns)
        if re.search(r'return (?:target |a )?(?:creature )?card from (?:your |a )graveyard', text) and not self.effect:
            if 'to the battlefield' in text:
                self.effect = self._make_graveyard_return_effect('battlefield')
            else:
                self.effect = self._make_graveyard_return_effect('hand')
            return
        
        # Search library (broad pattern)
        if re.search(r'search your library for', text) and not self.effect:
            # Default: search for a land (most common)
            self.effect = self._make_search_effect('land')
            return
        
        # === KEYWORD ACTION SPELL FALLTHROUGH ===
        # Surveil on spell (instants/sorceries with surveil)
        if self.surveil_amount > 0 and not self.effect:
            n = self.surveil_amount
            def make_surveil_spell(amount):
                def eff(game, card):
                    game.do_surveil(card.controller, amount)
                return eff
            self.effect = make_surveil_spell(n)
            return
        
        # Investigate on spell
        if self.is_investigate and not self.effect:
            def investigate_spell(game, card):
                game.do_investigate(card.controller)
            self.effect = investigate_spell
            return
        
        # Amass on spell
        if self.amass_count > 0 and not self.effect:
            n = self.amass_count
            atype = self.amass_type or 'Zombies'
            def make_amass_spell(amount, army_type):
                def eff(game, card):
                    game.do_amass(card.controller, amount, army_type)
                return eff
            self.effect = make_amass_spell(n, atype)
            return

    def _make_damage_effect(self, amount: int, target_type: str):
        def effect(game: 'Game', card: 'Card'):
            # X-cost damage: use card.x_value if amount is 0 or X-based
            actual_amount = getattr(card, 'x_value', amount) if amount == 0 else amount
            opp = game.players[(game.players.index(card.controller) + 1) % 2]
            
            # === AGENT-CHOSEN TARGET (Rule 601.2c) ===
            chosen = getattr(card, 'spell_target', None)
            if chosen is not None:
                from engine.player import Player
                if isinstance(chosen, Player):
                    chosen.life -= actual_amount
                    game.log_event(f"{card.name} deals {actual_amount} to {chosen.name} ({chosen.life} life)")
                elif hasattr(chosen, 'is_creature') and chosen.is_creature and chosen in game.battlefield.cards:
                    if not chosen.is_protected_from(card):
                        chosen.damage_taken += actual_amount
                        game.log_event(f"{card.name} deals {actual_amount} damage to {chosen.name} (dmg={chosen.damage_taken}/{chosen.toughness})")
                    else:
                        game.log_event(f"{card.name} fizzles — {chosen.name} has protection")
                elif hasattr(chosen, 'is_planeswalker') and chosen.is_planeswalker and chosen in game.battlefield.cards:
                    chosen.loyalty -= actual_amount
                    game.log_event(f"{card.name} deals {actual_amount} to {chosen.name} (loyalty→{chosen.loyalty})")
                else:
                    game.log_event(f"{card.name} fizzles — target is gone")
                card.spell_target = None
                return
            
            # === FALLBACK: auto-select (for sandbox/tests) ===
            if "each opponent" in target_type:
                opp.life -= actual_amount
                game.log_event(f"{card.name} deals {actual_amount} to {opp.name} ({opp.life} life)")
                return
            
            if ("any target" in target_type or "player" in target_type) and opp.life <= actual_amount:
                opp.life -= actual_amount
                game.log_event(f"{card.name} deals {actual_amount} to {opp.name} (LETHAL)")
                return
            
            if "any target" in target_type or "creature" in target_type:
                targets = [c for c in game.battlefield.cards 
                          if c.controller == opp and c.is_creature 
                          and not c.has_hexproof and not c.is_protected_from(card)]
                killable = [t for t in targets if (t.toughness or 0) - t.damage_taken <= actual_amount]
                if killable:
                    killable.sort(key=lambda c: c.power or 0, reverse=True)
                    t = killable[0]
                    t.damage_taken += actual_amount
                    game.log_event(f"{card.name} deals {actual_amount} damage to {t.name} (dmg={t.damage_taken}/{t.toughness})")
                    return
                if targets:
                    targets.sort(key=lambda c: c.power or 0, reverse=True)
                    t = targets[0]
                    t.damage_taken += actual_amount
                    game.log_event(f"{card.name} deals {actual_amount} damage to {t.name} (dmg={t.damage_taken}/{t.toughness})")
                    return
            
            if "any target" in target_type or "player" in target_type:
                opp.life -= actual_amount
                game.log_event(f"{card.name} deals {actual_amount} to {opp.name} ({opp.life} life)")
        return effect

    def _make_destroy_effect(self, target_type: str):
        def effect(game: 'Game', card: 'Card'):
            opp = game.players[(game.players.index(card.controller) + 1) % 2]
            
            # Agent-chosen target (Rule 601.2c)
            chosen = getattr(card, 'spell_target', None)
            if chosen is not None and hasattr(chosen, 'is_creature'):
                if chosen in game.battlefield.cards and not chosen.has_indestructible and not chosen.is_protected_from(card):
                    game.battlefield.remove(chosen); chosen.controller.graveyard.add(chosen)
                    game.log_event(f"{card.name} destroys {chosen.name}")
                    game._fire_death_trigger(chosen)
                else:
                    game.log_event(f"{card.name} fizzles — target is gone/indestructible")
                card.spell_target = None
                return
            
            # Fallback auto-select
            targets = [c for c in game.battlefield.cards 
                      if c.controller == opp and c.is_creature 
                      and not c.has_hexproof and not c.is_protected_from(card)
                      and not c.has_indestructible]
            if targets:
                targets.sort(key=lambda c: Card._threat_score(c), reverse=True)
                t = targets[0]
                game.battlefield.remove(t); t.controller.graveyard.add(t)
                game.log_event(f"{card.name} destroys {t.name}")
                game._fire_death_trigger(t)
        return effect
    
    @staticmethod
    def _threat_score(c: 'Card') -> float:
        """Rate how dangerous a creature is for targeting purposes."""
        score = float(c.power or 0)
        if c.has_flying: score += 2.0
        if c.has_lifelink: score += 1.5
        if c.has_deathtouch: score += 1.5
        if c.has_double_strike: score += 3.0
        if c.has_first_strike: score += 1.0
        if c.has_trample: score += 1.0
        if c.has_menace: score += 0.5
        if hasattr(c, 'static_effect') and c.static_effect: score += 3.0  # Lords/anthems
        if hasattr(c, 'death_effect') and c.death_effect: score += 1.0
        return score
    
    def _make_destroy_permanent_effect(self):
        def effect(game: 'Game', card: 'Card'):
            from engine.player import Player as _P
            opp = game.players[(game.players.index(card.controller) + 1) % 2]
            targets = [c for c in game.battlefield.cards 
                      if c.controller == opp and (c.is_enchantment or c.is_artifact) and not c.has_hexproof]
            if targets:
                # Prioritize: static effects > equipment > highest-CMC
                def perm_score(c):
                    s = 0.0
                    if hasattr(c, 'static_effect') and c.static_effect: s += 5.0
                    if hasattr(c, 'equip_cost') and c.equip_cost: s += 3.0
                    s += float(_P._parse_cmc(c.cost) if c.cost else 0)
                    return s
                targets.sort(key=perm_score, reverse=True)
                t = targets[0]
                game.battlefield.remove(t); t.controller.graveyard.add(t)
                game.log_event(f"{card.name} destroys {t.name}")
        return effect
    
    def _make_exile_effect(self):
        def effect(game: 'Game', card: 'Card'):
            opp = game.players[(game.players.index(card.controller) + 1) % 2]
            
            # Agent-chosen target
            chosen = getattr(card, 'spell_target', None)
            if chosen is not None and hasattr(chosen, 'is_creature'):
                if chosen in game.battlefield.cards and not chosen.is_protected_from(card):
                    game.battlefield.remove(chosen); game.exile.add(chosen)
                    game.log_event(f"{card.name} exiles {chosen.name}")
                else:
                    game.log_event(f"{card.name} fizzles — target is gone")
                card.spell_target = None
                return
            
            # Fallback auto-select
            targets = [c for c in game.battlefield.cards 
                      if c.controller == opp and c.is_creature 
                      and not c.has_hexproof and not c.is_protected_from(card)]
            if targets:
                def exile_priority(c):
                    s = Card._threat_score(c)
                    if c.has_indestructible: s += 10.0
                    if hasattr(c, 'death_effect') and c.death_effect: s += 3.0
                    return s
                targets.sort(key=exile_priority, reverse=True)
                t = targets[0]
                game.battlefield.remove(t); game.exile.add(t)
                game.log_event(f"{card.name} exiles {t.name}")
        return effect

    def _make_draw_effect(self, amount: int):
        def effect(game: 'Game', card: 'Card'):
            card.controller.draw_card(amount)
            game.log_event(f"{card.controller.name} draws {amount}")
        return effect
    
    def _make_lifegain_effect(self, amount: int):
        def effect(game: 'Game', card: 'Card'):
            card.controller.life += amount
            game.log_event(f"{card.controller.name} gains {amount} life ({card.controller.life})")
        return effect
        
    def _make_counter_effect(self):
        def effect(game: 'Game', card: 'Card'):
            # Agent-chosen target from spell_target
            chosen = getattr(card, 'spell_target', None)
            
            if chosen is not None:
                if isinstance(chosen, Card) and chosen in game.stack.cards:
                    # Counter a spell
                    game.stack.cards.remove(chosen)
                    (chosen.controller or game.active_player).graveyard.add(chosen)
                    game.log_event(f"{card.name} counters {chosen.name}!")
                elif isinstance(chosen, StackItem) and chosen in game.stack.cards:
                    # Stifle: counter a triggered/activated ability (Rule 702.37)
                    game.stack.cards.remove(chosen)
                    game.log_event(f"{card.name} counters ability: {chosen.description}!")
                else:
                    game.log_event(f"{card.name} fizzles — target no longer on stack")
                card.spell_target = None
                return
            
            # Fallback: counter top of stack
            if len(game.stack) > 0:
                target = game.stack.cards.pop()
                if isinstance(target, Card):
                    (target.controller or game.active_player).graveyard.add(target)
                    game.log_event(f"{card.name} counters {target.name}!")
                else:
                    game.stack.cards.append(target)
                    game.log_event(f"{card.name} fizzles (target is an ability)")
            else:
                game.log_event(f"{card.name} fizzles (no target)")
        return effect

    def _make_board_wipe_effect(self):
        def effect(game: 'Game', card: 'Card'):
            # Destroy all creatures (Rule 701.7) — except indestructible
            to_destroy = [c for c in game.battlefield.cards 
                         if c.is_creature and not c.has_indestructible]
            for creature in to_destroy:
                game.battlefield.remove(creature)
                creature.controller.graveyard.add(creature)
            game.log_event(f"{card.name} destroys {len(to_destroy)} creatures!")
        return effect
    
    def _make_buff_effect(self, p_buff: int, t_buff: int):
        def effect(game: 'Game', card: 'Card'):
            # Agent-chosen target
            chosen = getattr(card, 'spell_target', None)
            if chosen is not None and hasattr(chosen, 'is_creature'):
                if chosen in game.battlefield.cards:
                    chosen._temp_modifiers.append({'power': p_buff, 'toughness': t_buff})
                    game.log_event(f"{card.name} gives {chosen.name} +{p_buff}/+{t_buff} until end of turn ({chosen.power}/{chosen.toughness})")
                else:
                    game.log_event(f"{card.name} fizzles — target is gone")
                card.spell_target = None
                return
            
            # Fallback auto-select: buff the strongest friendly creature
            creatures = [c for c in game.battlefield.cards 
                        if c.controller == card.controller and c.is_creature]
            if creatures:
                creatures.sort(key=lambda c: c.power or 0, reverse=True)
                t = creatures[0]
                t._temp_modifiers.append({'power': p_buff, 'toughness': t_buff})
                game.log_event(f"{card.name} gives {t.name} +{p_buff}/+{t_buff} until end of turn ({t.power}/{t.toughness})")
        return effect

    def _make_debuff_effect(self, p_debuff: int, t_debuff: int):
        """Target creature gets -N/-N until end of turn."""
        def effect(game: 'Game', card: 'Card'):
            opp = game.players[(game.players.index(card.controller) + 1) % 2]
            
            # Agent-chosen target
            chosen = getattr(card, 'spell_target', None)
            if chosen is not None and hasattr(chosen, 'is_creature'):
                if chosen in game.battlefield.cards and not chosen.is_protected_from(card):
                    chosen._temp_modifiers.append({'power': -p_debuff, 'toughness': -t_debuff})
                    game.log_event(f"{card.name} gives {chosen.name} -{p_debuff}/-{t_debuff} until end of turn ({chosen.power}/{chosen.toughness})")
                else:
                    game.log_event(f"{card.name} fizzles — target is gone")
                card.spell_target = None
                return
            
            # Fallback auto-select
            targets = [c for c in game.battlefield.cards
                      if c.controller == opp and c.is_creature
                      and not c.has_hexproof and not c.is_protected_from(card)]
            if targets:
                targets.sort(key=lambda c: c.power or 0, reverse=True)
                t = targets[0]
                t._temp_modifiers.append({'power': -p_debuff, 'toughness': -t_debuff})
                game.log_event(f"{card.name} gives {t.name} -{p_debuff}/-{t_debuff} until end of turn ({t.power}/{t.toughness})")
        return effect

    def _make_discard_effect(self, count: int):
        """Target opponent discards N cards (heuristic: worst card)."""
        def effect(game: 'Game', card: 'Card'):
            opp = game.players[(game.players.index(card.controller) + 1) % 2]
            for _ in range(count):
                if len(opp.hand) == 0:
                    break
                hand = list(opp.hand.cards)
                # Discard worst: lowest-value non-land, or a land if all lands
                non_lands = [c for c in hand if not c.is_land]
                if non_lands:
                    worst = min(non_lands, key=lambda c: (c.power or 0) + (c.toughness or 0))
                else:
                    worst = hand[-1]
                opp.hand.remove(worst)
                opp.graveyard.add(worst)
                game.log_event(f"{card.name}: {opp.name} discards {worst.name}")
        return effect

    def _make_search_effect(self, card_type: str):
        """Search library for a card of given type, put in hand, shuffle."""
        def effect(game: 'Game', card: 'Card'):
            player = card.controller
            lib = list(player.library.cards)
            # Find matching cards
            matches = []
            for c in lib:
                if card_type in ('creature', 'creatures') and c.is_creature:
                    matches.append(c)
                elif card_type in ('land', 'lands', 'basic') and c.is_land:
                    matches.append(c)
                elif card_type in ('instant', 'instants') and c.is_instant:
                    matches.append(c)
                elif card_type in ('sorcery', 'sorceries') and c.is_sorcery:
                    matches.append(c)
                elif card_type in ('artifact', 'artifacts') and c.is_artifact:
                    matches.append(c)
                elif card_type in ('enchantment', 'enchantments') and c.is_enchantment:
                    matches.append(c)
                elif card_type == 'card':  # Generic "search for a card"
                    matches.append(c)
            
            if matches:
                # Pick best: highest CMC creature, or first land, etc.
                if matches[0].is_creature:
                    matches.sort(key=lambda c: (c.power or 0) + (c.toughness or 0), reverse=True)
                best = matches[0]
                player.library.remove(best)
                player.hand.add(best)
                player.shuffle_library()
                game.log_event(f"{card.name}: {player.name} searches library, finds {best.name}")
            else:
                player.shuffle_library()
                game.log_event(f"{card.name}: {player.name} searches library, finds nothing")
        return effect

    def _make_graveyard_return_effect(self, destination: str):
        """Return best creature card from graveyard to hand or battlefield."""
        def effect(game: 'Game', card: 'Card'):
            player = card.controller
            gy_creatures = [c for c in player.graveyard.cards if c.is_creature]
            if not gy_creatures:
                game.log_event(f"{card.name}: no creatures in {player.name}'s graveyard")
                return
            
            # Pick best creature
            gy_creatures.sort(key=lambda c: (c.power or 0) + (c.toughness or 0), reverse=True)
            best = gy_creatures[0]
            player.graveyard.remove(best)
            
            if destination == 'hand':
                player.hand.add(best)
                game.log_event(f"{card.name}: {player.name} returns {best.name} from graveyard to hand")
            elif destination == 'battlefield':
                best.controller = player
                best.summoning_sickness = True
                best.damage_taken = 0
                best.tapped = False
                game.battlefield.add(best)
                game.log_event(f"{card.name}: {player.name} returns {best.name} from graveyard to battlefield")
        return effect

    def _parse_flashback(self, text: str):
        """Parse Flashback {cost} from oracle text."""
        # Use original oracle_text (not lowered) to preserve mana symbol case
        original = self.oracle_text
        fb_match = re.search(r'[Ff]lashback ((?:\{[^}]+\})+)', original)
        if fb_match:
            self.flashback_cost = fb_match.group(1)

    def _parse_scry(self, text: str):
        """Parse Scry N from oracle text."""
        scry_match = re.search(r'scry (\d+)', text)
        if scry_match:
            self.scry_amount = int(scry_match.group(1))

    def _parse_planeswalker(self, text: str):
        """Parse Planeswalker loyalty and abilities (Rules 306, 606)."""
        if not self.is_planeswalker:
            return
        
        original = self.oracle_text
        
        # Parse starting loyalty — look for standalone number at end of oracle text
        # or from a dedicated 'loyalty' field (passed via constructor)
        # Scryfall format: loyalty is a separate field, but we also parse from text
        if self.loyalty == 0:
            # Try to find trailing loyalty number (e.g. "...loyalty 4" or just a bare number)
            loyalty_match = re.search(r'(?:^|\n)(\d+)\s*$', original.strip())
            if loyalty_match:
                self.loyalty = int(loyalty_match.group(1))
        
        # Parse loyalty abilities: [+N], [-N], [0] patterns
        # Format: "[+1]: Effect text" or "[−2]: Effect text"
        ability_pattern = re.compile(
            r'\[([+\-−]?\d+)\]\s*:\s*([^\[\n]+(?:\n(?!\[)[^\[\n]*)*)',
            re.MULTILINE
        )
        
        for match in ability_pattern.finditer(original):
            cost_str = match.group(1).replace('−', '-')  # Normalize minus sign
            cost = int(cost_str)
            effect_text = match.group(2).strip().lower()
            
            # Map effect text to actual effect functions
            effect_fn = self._map_pw_ability_effect(effect_text)
            
            self.loyalty_abilities.append({
                'cost': cost,
                'effect': effect_fn,
                'description': match.group(2).strip()
            })
    
    def _map_pw_ability_effect(self, text: str):
        """Map planeswalker ability text to an effect function."""
        # Damage effects
        dmg_match = re.search(r'deals? (\d+) damage to (any target|target creature|target player|each opponent)', text)
        if dmg_match:
            amount = int(dmg_match.group(1))
            target_type = dmg_match.group(2)
            return self._make_damage_effect(amount, target_type)
        
        # Draw cards
        draw_match = re.search(r'draw (\d+) cards?', text)
        if draw_match:
            amount = int(draw_match.group(1))
            return self._make_draw_effect(amount)
        
        # Gain life
        life_match = re.search(r'(?:you )?gain (\d+) life', text)
        if life_match:
            amount = int(life_match.group(1))
            return self._make_lifegain_effect(amount)
        
        # Destroy target creature
        if 'destroy target creature' in text:
            return self._make_destroy_effect('creature')
        
        # Create token
        token_match = re.search(r'create (?:a |an? )?(\d+)/(\d+).*token', text)
        if token_match:
            tp = int(token_match.group(1))
            tt = int(token_match.group(2))
            def token_effect(game, card, _tp=tp, _tt=tt):
                from engine.card import Card
                token = Card(name="Token", cost="", type_line="Creature — Token",
                            base_power=_tp, base_toughness=_tt, oracle_text="")
                token.controller = card.controller
                token.summoning_sickness = True
                game.battlefield.add(token)
                game.log_event(f"{card.name}: creates {_tp}/{_tt} token")
            return token_effect
        
        # Discard
        if 'discards' in text:
            return self._make_discard_effect(1)
        
        # Return from graveyard
        if 'return' in text and 'graveyard' in text:
            if 'battlefield' in text:
                return self._make_graveyard_return_effect('battlefield')
            return self._make_graveyard_return_effect('hand')
        
        # +N/+N buff
        buff_match = re.search(r'gets? \+(\d+)/\+(\d+)', text)
        if buff_match:
            p = int(buff_match.group(1))
            t = int(buff_match.group(2))
            return self._make_buff_effect(p, t)
        
        # Fallback: generic "gain 1 life" as a no-op placeholder
        def noop_effect(game, card):
            game.log_event(f"{card.name}: ability resolves (no mapped effect)")
        return noop_effect

    # ─── Tier 5: Advanced Mechanics Parsing ────────────────────────

    def _parse_landfall(self, text: str):
        """Parse landfall triggers (Rule 702.52)."""
        if 'landfall' not in text and 'whenever a land enters the battlefield under your control' not in text:
            return
        
        # Common landfall effects
        dmg_match = re.search(r'landfall.*?deals? (\d+) damage', text)
        if dmg_match:
            amount = int(dmg_match.group(1))
            def landfall_dmg(game, card, amt=amount):
                opp = game.players[(game.players.index(card.controller) + 1) % 2]
                opp.life -= amt
                game.log_event(f"Landfall: {card.name} deals {amt} to {opp.name} ({opp.life} life)")
            self.landfall_effect = landfall_dmg
            return
        
        life_match = re.search(r'landfall.*?gain (\d+) life', text)
        if life_match:
            amount = int(life_match.group(1))
            def landfall_life(game, card, amt=amount):
                card.controller.life += amt
                game.log_event(f"Landfall: {card.name} gains {amt} life ({card.controller.life})")
            self.landfall_effect = landfall_life
            return
        
        buff_match = re.search(r'landfall.*?gets? \+(\d+)/\+(\d+)', text)
        if buff_match:
            p, t = int(buff_match.group(1)), int(buff_match.group(2))
            def landfall_buff(game, card, _p=p, _t=t):
                card._temp_modifiers.append({'power': _p, 'toughness': _t})
                game.log_event(f"Landfall: {card.name} gets +{_p}/+{_t}")
            self.landfall_effect = landfall_buff
            return
        
        draw_match = re.search(r'landfall.*?draw (?:a )?card', text)
        if draw_match:
            def landfall_draw(game, card):
                card.controller.draw_card(1)
                game.log_event(f"Landfall: {card.name} draws a card")
            self.landfall_effect = landfall_draw
            return
        
        # Generic landfall: +1/+1 counter
        counter_match = re.search(r'landfall.*?put.*?\+1/\+1 counter', text)
        if counter_match:
            def landfall_counter(game, card):
                card.counters['+1/+1'] = card.counters.get('+1/+1', 0) + 1
                game.log_event(f"Landfall: {card.name} gets +1/+1 counter ({card.counters['+1/+1']} total)")
            self.landfall_effect = landfall_counter
            return

    def _parse_attack_trigger(self, text: str):
        """Parse 'whenever ~ attacks' triggers (Rule 508.1a)."""
        name_ref = self.name.lower().split(',')[0].strip()
        
        # Check multiple patterns for attack triggers
        has_attack = (
            re.search(rf'whenever {re.escape(name_ref)} attacks', text) or
            'whenever this creature attacks' in text or
            re.search(r'whenever .* attacks', text) and self.is_creature or
            'enters or attacks' in text
        )
        if not has_attack:
            return
        
        # Parse what happens on attack
        dmg_match = re.search(r'attacks.*deals? (\d+) damage', text)
        if dmg_match:
            amount = int(dmg_match.group(1))
            def atk_dmg(game, card, amt=amount):
                opp = game.players[(game.players.index(card.controller) + 1) % 2]
                opp.life -= amt
                game.log_event(f"Attack trigger: {card.name} deals {amt} to {opp.name}")
            self.attack_trigger = atk_dmg
            return
        
        # +1/+1 until end of turn on attack
        buff_match = re.search(r'attacks.*gets? \+(\d+)/\+(\d+)', text)
        if buff_match:
            p, t = int(buff_match.group(1)), int(buff_match.group(2))
            def atk_buff(game, card, _p=p, _t=t):
                card._temp_modifiers.append({'power': _p, 'toughness': _t})
                game.log_event(f"Attack trigger: {card.name} gets +{_p}/+{_t}")
            self.attack_trigger = atk_buff
            return
        
        # Create token on attack
        token_match = re.search(r'attacks.*create.*?(\d+)/(\d+).*token', text)
        if token_match:
            tp, tt = int(token_match.group(1)), int(token_match.group(2))
            def atk_token(game, card, _tp=tp, _tt=tt):
                from engine.card import Card
                token = Card(name="Token", cost="", type_line="Creature — Token",
                            base_power=_tp, base_toughness=_tt, oracle_text="")
                token.controller = card.controller
                token.summoning_sickness = True
                game.battlefield.add(token)
                game.log_event(f"Attack trigger: {card.name} creates {_tp}/{_tt} token")
            self.attack_trigger = atk_token
            return
        
        # Draw a card on attack
        if re.search(r'attacks.*draw (?:a |)(card|\d+)', text):
            def atk_draw(game, card):
                card.controller.draw_card(1, game=game)
                game.log_event(f"Attack trigger: {card.name} — draw a card")
            self.attack_trigger = atk_draw
            return
        
        # Exile target on attack
        if re.search(r'attacks.*exile', text):
            def atk_exile(game, card):
                opp = game.players[(game.players.index(card.controller) + 1) % 2]
                targets = [c for c in game.battlefield.cards if c.controller == opp and c.is_creature
                          and not c.has_hexproof]
                if targets:
                    t = max(targets, key=lambda c: (c.power or 0))
                    game.battlefield.remove(t)
                    opp.exile.add(t)
                    game.log_event(f"Attack trigger: {card.name} exiles {t.name}")
            self.attack_trigger = atk_exile
            self.is_removal = True
            return
        
        # Life drain on attack
        life_drain = re.search(r'attacks.*(?:loses?|drain) (\d+) life', text)
        if life_drain:
            amount = int(life_drain.group(1))
            def atk_drain(game, card, amt=amount):
                opp = game.players[(game.players.index(card.controller) + 1) % 2]
                opp.life -= amt
                card.controller.life += amt
                game.log_event(f"Attack trigger: {card.name} drains {amt} from {opp.name}")
            self.attack_trigger = atk_drain
            return
        
        # Mill on attack
        mill_match = re.search(r'attacks.*mills? (\d+)', text)
        if mill_match:
            amount = int(mill_match.group(1))
            def atk_mill(game, card, amt=amount):
                opp = game.players[(game.players.index(card.controller) + 1) % 2]
                for _ in range(amt):
                    if opp.library.cards:
                        milled = opp.library.draw()
                        if milled: opp.graveyard.add(milled)
                game.log_event(f"Attack trigger: {card.name} — {opp.name} mills {amt}")
            self.attack_trigger = atk_mill
            return
        
        # Put counter on attack
        if re.search(r'attacks.*\+1/\+1 counter', text):
            def atk_counter(game, card):
                card.counters['+1/+1'] = card.counters.get('+1/+1', 0) + 1
                game.log_event(f"Attack trigger: {card.name} gets a +1/+1 counter")
            self.attack_trigger = atk_counter
            return

    def _parse_combat_damage_trigger(self, text: str):
        """Parse 'whenever ~ deals combat damage to a player' triggers."""
        if 'deals combat damage to a player' not in text and 'deals combat damage to an opponent' not in text:
            return
        
        # Draw a card on combat damage
        if 'draw' in text:
            def cd_draw(game, card):
                card.controller.draw_card(1)
                game.log_event(f"Combat damage trigger: {card.name} — draw a card")
            self.combat_damage_trigger = cd_draw
            return
        
        # Discard on combat damage
        if 'discard' in text:
            def cd_discard(game, card):
                opp = game.players[(game.players.index(card.controller) + 1) % 2]
                if opp.hand.cards:
                    worst = min(opp.hand.cards, key=lambda c: (c.power or 0) + (c.toughness or 0))
                    opp.hand.remove(worst)
                    opp.graveyard.add(worst)
                    game.log_event(f"Combat damage trigger: {card.name} — {opp.name} discards {worst.name}")
            self.combat_damage_trigger = cd_discard
            return
        
        # Deal extra damage
        dmg_match = re.search(r'combat damage.*deals? (\d+).*damage', text)
        if dmg_match:
            amount = int(dmg_match.group(1))
            def cd_damage(game, card, amt=amount):
                opp = game.players[(game.players.index(card.controller) + 1) % 2]
                opp.life -= amt
                game.log_event(f"Combat damage trigger: {card.name} deals {amt} extra damage")
            self.combat_damage_trigger = cd_damage
            return
        
        # +1/+1 counter on combat damage
        if '+1/+1 counter' in text:
            def cd_counter(game, card):
                card.counters['+1/+1'] = card.counters.get('+1/+1', 0) + 1
                game.log_event(f"Combat damage trigger: {card.name} gets +1/+1 counter")
            self.combat_damage_trigger = cd_counter
            return
        
        # Exile on combat damage
        if 'exile' in text:
            def cd_exile(game, card):
                opp = game.players[(game.players.index(card.controller) + 1) % 2]
                if opp.library.cards:
                    exiled = opp.library.draw()
                    if exiled: opp.exile.add(exiled)
                    game.log_event(f"Combat damage trigger: {card.name} exiles from library")
            self.combat_damage_trigger = cd_exile
            return
        
        # Mill on combat damage
        mill_match = re.search(r'mills? (\d+)', text)
        if mill_match:
            amt = int(mill_match.group(1))
            def cd_mill(game, card, amount=amt):
                opp = game.players[(game.players.index(card.controller) + 1) % 2]
                for _ in range(amount):
                    if opp.library.cards:
                        m = opp.library.draw()
                        if m: opp.graveyard.add(m)
                game.log_event(f"Combat damage trigger: {card.name} mills {amount}")
            self.combat_damage_trigger = cd_mill
            return
        
        # Create token on combat damage
        tok_match = re.search(r'create.*?(\d+)/(\d+)', text)
        if tok_match:
            tp, tt = int(tok_match.group(1)), int(tok_match.group(2))
            def cd_token(game, card, _tp=tp, _tt=tt):
                from engine.card import Card
                token = Card(name="Token", cost="", type_line="Token Creature",
                            base_power=_tp, base_toughness=_tt, oracle_text="")
                token.controller = card.controller
                token.summoning_sickness = True
                game.battlefield.add(token)
                game.log_event(f"Combat damage trigger: {card.name} creates {_tp}/{_tt} token")
            self.combat_damage_trigger = cd_token
            return
        
        # Gain life on combat damage
        life_match = re.search(r'gain (\d+) life', text)
        if life_match:
            amt = int(life_match.group(1))
            def cd_life(game, card, amount=amt):
                card.controller.life += amount
                game.log_event(f"Combat damage trigger: {card.name} gains {amount} life")
            self.combat_damage_trigger = cd_life
            return

    def _parse_kicker(self, text: str):
        """Parse kicker {cost} (Rule 702.32)."""
        # Use original oracle_text for mana symbol case
        original = self.oracle_text
        kicker_match = re.search(r'[Kk]icker ((?:\{[^}]+\})+)', original)
        if not kicker_match:
            return
        
        self.kicker_cost = kicker_match.group(1)
        
        # Parse kicked effect (the enhanced/additional effect)
        lower = text
        
        # "If ~ was kicked, [effect]"
        kicked_text = re.search(r'if .* was kicked[,.]?\s*(.*?)(?:\.|$)', lower)
        if not kicked_text:
            kicked_text = re.search(r'when .* kicked[,.]?\s*(.*?)(?:\.|$)', lower)
        
        if kicked_text:
            effect_text = kicked_text.group(1).strip()
            
            # Parse the kicked effect
            dmg_match = re.search(r'deals? (\d+) damage', effect_text)
            if dmg_match:
                amount = int(dmg_match.group(1))
                def kick_dmg(game, card, amt=amount):
                    opp = game.players[(game.players.index(card.controller) + 1) % 2]
                    opp.life -= amt
                    game.log_event(f"Kicker: {card.name} deals {amt} extra damage")
                self.kicker_effect = kick_dmg
                return
            
            if 'draw' in effect_text:
                draw_match = re.search(r'draw (\d+)', effect_text)
                n = int(draw_match.group(1)) if draw_match else 1
                def kick_draw(game, card, amt=n):
                    card.controller.draw_card(amt)
                    game.log_event(f"Kicker: {card.name} draws {amt}")
                self.kicker_effect = kick_draw
                return
            
            if 'destroy' in effect_text:
                def kick_destroy(game, card):
                    opp = game.players[(game.players.index(card.controller) + 1) % 2]
                    targets = [c for c in game.battlefield.cards if c.controller == opp and c.is_creature]
                    if targets:
                        t = max(targets, key=lambda c: c.power or 0)
                        game.battlefield.remove(t)
                        t.controller.graveyard.add(t)
                        game.log_event(f"Kicker: {card.name} destroys {t.name}")
                self.kicker_effect = kick_destroy
                return
            
            # Counter on self
            if '+1/+1 counter' in effect_text:
                def kick_counter(game, card):
                    card.counters['+1/+1'] = card.counters.get('+1/+1', 0) + 1
                    game.log_event(f"Kicker: {card.name} gets +1/+1 counter")
                self.kicker_effect = kick_counter
                return

    # ─── Tier 6: Utility Mechanics ─────────────────────────────────

    def _make_fight_effect(self):
        """Target creature you control fights target creature opponent controls (Rule 701.12)."""
        def effect(game: 'Game', card: 'Card'):
            opp = game.players[(game.players.index(card.controller) + 1) % 2]
            # Pick best own creature and best opposing creature
            own = [c for c in game.battlefield.cards
                  if c.controller == card.controller and c.is_creature]
            enemies = [c for c in game.battlefield.cards
                      if c.controller == opp and c.is_creature
                      and not c.has_hexproof and not c.is_protected_from(card)]
            if own and enemies:
                fighter = max(own, key=lambda c: c.power or 0)
                target = max(enemies, key=lambda c: c.power or 0)
                # Each deals damage equal to its power to the other
                target.damage_taken += (fighter.power or 0)
                fighter.damage_taken += (target.power or 0)
                if fighter.has_deathtouch:
                    target.deathtouch_damaged = True
                if target.has_deathtouch:
                    fighter.deathtouch_damaged = True
                game.log_event(f"{card.name}: {fighter.name} fights {target.name} "
                             f"({fighter.power} ↔ {target.power})")
        return effect

    def _make_mill_effect(self, amount: int):
        """Target player mills N cards (Rule 701.13)."""
        def effect(game: 'Game', card: 'Card'):
            opp = game.players[(game.players.index(card.controller) + 1) % 2]
            milled = 0
            for _ in range(amount):
                if opp.library.cards:
                    top = opp.library.cards.pop(0)
                    opp.graveyard.add(top)
                    milled += 1
            game.log_event(f"{card.name}: {opp.name} mills {milled} cards "
                         f"({len(opp.library)} remaining)")
        return effect

    def _make_proliferate_effect(self):
        """Proliferate: add a counter to each permanent/player with counters (Rule 701.27)."""
        def effect(game: 'Game', card: 'Card'):
            game.do_proliferate(card.controller)
        return effect

    def _parse_cycling(self, text: str):
        """Parse cycling {cost} (Rule 702.28)."""
        original = self.oracle_text
        cycling_match = re.search(r'[Cc]ycling ((?:\{[^}]+\})+)', original)
        if not cycling_match:
            return
        
        self.cycling_cost = cycling_match.group(1)

    # ─── Tier 7: Combat & Economy ──────────────────────────────────

    def _make_bounce_spell_effect(self):
        """Return target creature to its owner's hand (Rule 701.4)."""
        def effect(game: 'Game', card: 'Card'):
            opp = game.players[(game.players.index(card.controller) + 1) % 2]
            targets = [c for c in game.battlefield.cards
                      if c.controller == opp and c.is_creature
                      and not c.has_hexproof and not c.is_protected_from(card)]
            if targets:
                # Bounce the biggest threat
                t = max(targets, key=lambda c: (c.power or 0) + (c.toughness or 0))
                game.battlefield.remove(t)
                opp.hand.add(t)
                game.log_event(f"{card.name} bounces {t.name} to {opp.name}'s hand")
        return effect

    def _make_sac_creature_damage_effect(self, amount: int):
        """Sacrifice a creature, then deal N damage."""
        def effect(game: 'Game', card: 'Card'):
            opp = game.players[(game.players.index(card.controller) + 1) % 2]
            # Agent-chosen sacrifice target
            chosen_sac = getattr(card, 'spell_target', None)
            own = [c for c in game.battlefield.cards
                  if c.controller == card.controller and c.is_creature]
            if chosen_sac and hasattr(chosen_sac, 'is_creature') and chosen_sac in game.battlefield.cards:
                sac = chosen_sac
            elif own:
                sac = min(own, key=lambda c: c.power or 0)
            else:
                game.log_event(f"{card.name}: no creature to sacrifice — fizzles")
                return
            game.battlefield.remove(sac)
            card.controller.graveyard.add(sac)
            game._fire_death_trigger(sac)
            game.log_event(f"{card.name}: sacrifices {sac.name}")
            # Deal damage
            opp.life -= amount
            game.log_event(f"{card.name} deals {amount} to {opp.name} ({opp.life} life)")
        return effect

    def _make_sac_creature_draw_effect(self, count: int):
        """Sacrifice a creature, then draw N cards."""
        def effect(game: 'Game', card: 'Card'):
            # Agent-chosen sacrifice target
            chosen_sac = getattr(card, 'spell_target', None)
            own = [c for c in game.battlefield.cards
                  if c.controller == card.controller and c.is_creature]
            if chosen_sac and hasattr(chosen_sac, 'is_creature') and chosen_sac in game.battlefield.cards:
                sac = chosen_sac
            elif own:
                sac = min(own, key=lambda c: c.power or 0)
            else:
                game.log_event(f"{card.name}: no creature to sacrifice — fizzles")
                return
            game.battlefield.remove(sac)
            card.controller.graveyard.add(sac)
            game._fire_death_trigger(sac)
            game.log_event(f"{card.name}: sacrifices {sac.name}")
            card.controller.draw_card(count)
            game.log_event(f"{card.name}: {card.controller.name} draws {count}")
        return effect

    def _make_treasure_effect(self, count: int):
        """Create N Treasure tokens (artifact tokens that produce mana)."""
        def effect(game: 'Game', card: 'Card'):
            for _ in range(count):
                token = Card(name="Treasure", cost="",
                           type_line="Token Artifact — Treasure")
                token.is_treasure = True
                token.controller = card.controller
                game.battlefield.add(token)
            game.log_event(f"{card.name}: creates {count} Treasure token(s)")
        return effect

    def _parse_vehicle(self, text: str):
        """Parse Vehicle type and Crew N (Rule 702.122)."""
        if 'vehicle' not in (self.type_line or '').lower():
            return
        self.is_vehicle = True
        crew_match = re.search(r'crew (\d+)', text)
        if crew_match:
            self.crew_cost = int(crew_match.group(1))

    def _parse_spacecraft(self, text: str):
        """Parse Spacecraft type and Crew-like mechanics."""
        if 'spacecraft' not in (self.type_line or '').lower():
            return
            
        self.is_vehicle = True  # Engine uses is_vehicle for crewing logic
        self.is_spacecraft = True
        
        # Spacecraft crew cost (could be explicit, but usually assume 2 if missing from parser)
        crew_match = re.search(r'crew (\d+)', text)
        if crew_match:
            self.crew_cost = int(crew_match.group(1))
        else:
            self.crew_cost = getattr(self, 'crew_cost', 2)
            
        # Treat Spacecraft as Vehicles that fly or have specific evasion (as per prompt)
        if 'flying' in text:
            self.has_flying = True

    def _parse_class(self, text: str):
        """Parse Class enchantments and their leveling mechanics."""
        if 'class' not in (self.type_line or '').lower() or not self.is_enchantment:
            return
            
        self.is_class = True
        self.class_level = 1
        
        # Level 2 parsing
        level_2_match = re.search(r'\{([^}]+)\}: level 2', text)
        if level_2_match:
            cost = level_2_match.group(1).upper()
            def level_2_effect(game, card, c=cost):
                if card.class_level >= 2: return
                card.class_level = 2
                game.log_event(f"Ability: {card.name} levels up to Level 2")
            self.activated_abilities.append({
                'cost_tap': False, 'cost_mana': f"{{{cost}}}", 'cost_sacrifice': False,
                'effect': level_2_effect, 'description': f"Level 2",
                'is_class_level': True, 'level_target': 2
            })
            
        # Level 3 parsing
        level_3_match = re.search(r'\{([^}]+)\}: level 3', text)
        if level_3_match:
            cost = level_3_match.group(1).upper()
            def level_3_effect(game, card, c=cost):
                if card.class_level >= 3: return
                card.class_level = 3
                game.log_event(f"Ability: {card.name} levels up to Level 3")
            self.activated_abilities.append({
                'cost_tap': False, 'cost_mana': f"{{{cost}}}", 'cost_sacrifice': False,
                'effect': level_3_effect, 'description': f"Level 3",
                'is_class_level': True, 'level_target': 3
            })

    def _parse_prowess(self, text: str):
        """Parse prowess keyword (Rule 702.107)."""
        if 'prowess' in text.split():
            self.has_prowess = True

    def _parse_marvel(self, text: str):
        """Parse Marvel Universes Beyond mechanics (e.g., Heroic, Assemble)."""
        if 'assemble' in text:
            self.has_assemble = True
        if 'heroic' in text:
            self.has_heroic = True

    def _parse_tmnt(self, text: str):
        """Parse TMNT Universes Beyond mechanics (e.g., Ninjutsu, Mutate)."""
        if 'ninjutsu' in text:
            self.has_ninjutsu = True
        if 'mutate' in text:
            self.has_mutate = True

    def _parse_cast_trigger(self, text: str):
        """Parse 'whenever you cast' triggers (e.g., prowess-like, storm-like)."""
        if 'whenever you cast' not in text:
            return
        
        # "whenever you cast a noncreature spell" / "whenever you cast a spell"
        # Give creatures buff, draw, damage, or counter based on effect text
        if 'draw' in text:
            def cast_draw(game, card):
                card.controller.draw_card(1, game=game)
                game.log_event(f"Cast trigger: {card.name} — draw a card")
            self.cast_trigger = cast_draw
            return
        
        if re.search(r'gets? \+(\d+)/\+(\d+)', text):
            m = re.search(r'gets? \+(\d+)/\+(\d+)', text)
            p, t = int(m.group(1)), int(m.group(2))
            def cast_buff(game, card, _p=p, _t=t):
                card._temp_modifiers.append({'power': _p, 'toughness': _t})
                game.log_event(f"Cast trigger: {card.name} gets +{_p}/+{_t}")
            self.cast_trigger = cast_buff
            return
        
        if re.search(r'deals? (\d+) damage', text):
            m = re.search(r'deals? (\d+) damage', text)
            amt = int(m.group(1))
            def cast_dmg(game, card, amount=amt):
                opp = game.players[(game.players.index(card.controller) + 1) % 2]
                opp.life -= amount
                game.log_event(f"Cast trigger: {card.name} deals {amount} to {opp.name}")
            self.cast_trigger = cast_dmg
            return
        
        if '+1/+1 counter' in text:
            def cast_counter(game, card):
                card.counters['+1/+1'] = card.counters.get('+1/+1', 0) + 1
                game.log_event(f"Cast trigger: {card.name} gets +1/+1 counter")
            self.cast_trigger = cast_counter
            return
        
        if 'gain' in text and 'life' in text:
            m = re.search(r'gain (\d+) life', text)
            amt = int(m.group(1)) if m else 1
            def cast_life(game, card, amount=amt):
                card.controller.life += amount
                game.log_event(f"Cast trigger: {card.name} — gain {amount} life")
            self.cast_trigger = cast_life
            return
        
        if 'create' in text and 'token' in text:
            tok = re.search(r'create.*?(\d+)/(\d+)', text)
            if tok:
                tp, tt = int(tok.group(1)), int(tok.group(2))
                def cast_token(game, card, _tp=tp, _tt=tt):
                    from engine.card import Card
                    token = Card(name="Token", cost="", type_line="Token Creature",
                                base_power=_tp, base_toughness=_tt, oracle_text="")
                    token.controller = card.controller
                    token.summoning_sickness = True
                    game.battlefield.add(token)
                    game.log_event(f"Cast trigger: {card.name} creates {_tp}/{_tt} token")
                self.cast_trigger = cast_token
                return

    def _parse_block_trigger(self, text: str):
        """Parse 'whenever ~ blocks' or 'whenever ~ blocks or becomes blocked' triggers."""
        name_ref = self.name.lower().split(',')[0].strip()
        if not (re.search(rf'whenever {re.escape(name_ref)} blocks', text) or
                'whenever this creature blocks' in text or 
                'whenever .* blocks or becomes blocked' in text):
            return
        
        # Buff on block
        buff_match = re.search(r'blocks.*gets? \+(\d+)/\+(\d+)', text)
        if buff_match:
            p, t = int(buff_match.group(1)), int(buff_match.group(2))
            def block_buff(game, card, _p=p, _t=t):
                card._temp_modifiers.append({'power': _p, 'toughness': _t})
                game.log_event(f"Block trigger: {card.name} gets +{_p}/+{_t}")
            self.block_trigger = block_buff
            return
        
        # Damage on block
        dmg_match = re.search(r'blocks.*deals? (\d+) damage', text)
        if dmg_match:
            amt = int(dmg_match.group(1))
            def block_dmg(game, card, amount=amt):
                opp = game.players[(game.players.index(card.controller) + 1) % 2]
                opp.life -= amount
                game.log_event(f"Block trigger: {card.name} deals {amount}")
            self.block_trigger = block_dmg
            return
        
        # Draw on block
        if 'draw' in text:
            def block_draw(game, card):
                card.controller.draw_card(1, game=game)
                game.log_event(f"Block trigger: {card.name} — draw a card")
            self.block_trigger = block_draw
            return
        
        # +1/+1 counter on block
        if '+1/+1 counter' in text:
            def block_counter(game, card):
                card.counters['+1/+1'] = card.counters.get('+1/+1', 0) + 1
                game.log_event(f"Block trigger: {card.name} gets +1/+1 counter")
            self.block_trigger = block_counter
            return

    def _parse_mana_dork(self, text: str):
        """Parse creatures with '{T}: Add mana' abilities (mana dorks)."""
        if not self.is_creature:
            return
        if '{t}:' not in text:
            return
        # Check for mana production
        if re.search(r'{t}:?\s*add\s', text):
            self.is_mana_dork = True

    def _parse_tap_ability(self, text: str):
        """Parse '{T}: effect' activated abilities on creatures (Rule 602)."""
        if not self.is_creature or self.is_mana_dork:
            return
        if '{t}:' not in text and '{t},' not in text:
            return
        # Already has an activated ability from another parser
        if getattr(self, 'activated_abilities', None):
            return
        
        # Extract the part after {t}: 
        tap_text = text.split('{t}')[1] if '{t}' in text else ''
        
        # Damage
        dmg_match = re.search(r'deals? (\d+) damage', tap_text)
        if dmg_match:
            amt = int(dmg_match.group(1))
            def tap_dmg(game, card, amount=amt):
                if card.tapped: return
                card.tapped = True
                opp = game.players[(game.players.index(card.controller) + 1) % 2]
                opp.life -= amount
                game.log_event(f"Tap: {card.name} deals {amount} damage")
            self.tap_ability_effect = tap_dmg
            return
        
        # Draw
        if 'draw' in tap_text:
            def tap_draw(game, card):
                if card.tapped: return
                card.tapped = True
                card.controller.draw_card(1, game=game)
                game.log_event(f"Tap: {card.name} — draw a card")
            self.tap_ability_effect = tap_draw
            return
        
        # Destroy
        if 'destroy' in tap_text:
            def tap_destroy(game, card):
                if card.tapped: return
                card.tapped = True
                opp = game.players[(game.players.index(card.controller) + 1) % 2]
                targets = [c for c in game.battlefield.cards if c.controller == opp and c.is_creature
                          and not c.has_hexproof]
                if targets:
                    t = max(targets, key=lambda c: (c.power or 0))
                    game.battlefield.remove(t)
                    if not t.is_token: opp.graveyard.add(t)
                    game.log_event(f"Tap: {card.name} destroys {t.name}")
            self.tap_ability_effect = tap_destroy
            self.is_removal = True
            return
        
        # Tap/untap target
        if 'tap target' in tap_text or 'untap target' in tap_text:
            def tap_tap_target(game, card):
                if card.tapped: return
                card.tapped = True
                opp = game.players[(game.players.index(card.controller) + 1) % 2]
                targets = [c for c in game.battlefield.cards if c.controller == opp and c.is_creature
                          and not c.tapped]
                if targets:
                    t = max(targets, key=lambda c: (c.power or 0))
                    t.tapped = True
                    game.log_event(f"Tap: {card.name} taps {t.name}")
            self.tap_ability_effect = tap_tap_target
            return
        
        # Create token
        tok_match = re.search(r'create.*?(\d+)/(\d+)', tap_text)
        if tok_match:
            tp, tt = int(tok_match.group(1)), int(tok_match.group(2))
            def tap_token(game, card, _tp=tp, _tt=tt):
                if card.tapped: return
                card.tapped = True
                from engine.card import Card
                token = Card(name="Token", cost="", type_line="Token Creature",
                            base_power=_tp, base_toughness=_tt, oracle_text="")
                token.controller = card.controller
                token.summoning_sickness = True
                game.battlefield.add(token)
                game.log_event(f"Tap: {card.name} creates {_tp}/{_tt} token")
            self.tap_ability_effect = tap_token
            return
        
        # +1/+1 counter
        if '+1/+1 counter' in tap_text:
            def tap_counter(game, card):
                if card.tapped: return
                card.tapped = True
                card.counters['+1/+1'] = card.counters.get('+1/+1', 0) + 1
                game.log_event(f"Tap: {card.name} gets +1/+1 counter")
            self.tap_ability_effect = tap_counter
            return
        
        # Mill
        mill_match = re.search(r'mills? (\d+)', tap_text)
        if mill_match:
            amt = int(mill_match.group(1))
            def tap_mill(game, card, amount=amt):
                if card.tapped: return
                card.tapped = True
                opp = game.players[(game.players.index(card.controller) + 1) % 2]
                for _ in range(amount):
                    if opp.library.cards:
                        m = opp.library.draw()
                        if m: opp.graveyard.add(m)
                game.log_event(f"Tap: {card.name} mills {amount}")
            self.tap_ability_effect = tap_mill
            return
        
        # Exile
        if 'exile' in tap_text:
            def tap_exile(game, card):
                if card.tapped: return
                card.tapped = True
                opp = game.players[(game.players.index(card.controller) + 1) % 2]
                targets = [c for c in game.battlefield.cards if c.controller == opp and c.is_creature
                          and not c.has_hexproof]
                if targets:
                    t = max(targets, key=lambda c: (c.power or 0))
                    game.battlefield.remove(t)
                    opp.exile.add(t)
                    game.log_event(f"Tap: {card.name} exiles {t.name}")
            self.tap_ability_effect = tap_exile
            self.is_removal = True
            return
        
        # Return from graveyard
        if 'return' in tap_text and 'graveyard' in tap_text:
            def tap_return(game, card):
                if card.tapped: return
                card.tapped = True
                player = card.controller
                creatures = [c for c in player.graveyard.cards if c.is_creature]
                if creatures:
                    best = max(creatures, key=lambda c: (c.power or 0) + (c.toughness or 0))
                    player.graveyard.remove(best)
                    player.hand.add(best)
                    game.log_event(f"Tap: {card.name} returns {best.name} to hand")
            self.tap_ability_effect = tap_return
            return
        
        # Life gain
        life_match = re.search(r'gain (\d+) life', tap_text)
        if life_match:
            amt = int(life_match.group(1))
            def tap_life(game, card, amount=amt):
                if card.tapped: return
                card.tapped = True
                card.controller.life += amount
                game.log_event(f"Tap: {card.name} gains {amount} life")
            self.tap_ability_effect = tap_life
            return
        
        # Opponent loses life
        lose_match = re.search(r'(?:target\s+)?(?:player|opponent)\s+loses?\s+(\d+)\s+life', tap_text)
        if lose_match:
            amt = int(lose_match.group(1))
            def tap_lose_life(game, card, amount=amt):
                if card.tapped: return
                card.tapped = True
                opp = game.players[(game.players.index(card.controller) + 1) % 2]
                opp.life -= amount
                game.log_event(f"Tap: {card.name} — opponent loses {amount} life")
            self.tap_ability_effect = tap_lose_life
            return
        
        # Sacrifice self for effect
        if 'sacrifice' in tap_text:
            def tap_sac(game, card):
                if card.tapped: return
                card.tapped = True
                game.battlefield.remove(card)
                if not card.is_token: card.controller.graveyard.add(card)
                game.log_event(f"Tap: {card.name} sacrificed for effect")
            self.tap_ability_effect = tap_sac
            return
        
        # Scry
        if 'scry' in tap_text:
            def tap_scry(game, card):
                if card.tapped: return
                card.tapped = True
                game.log_event(f"Tap: {card.name} — scry")
            self.tap_ability_effect = tap_scry
            return
        
        # Generic fallback — mark as having an activated ability
        self.has_activated_ability = True

    def _parse_enchantment_trigger(self, text: str):
        """Parse enchantment 'whenever' and 'at the beginning' triggers."""
        if not self.is_enchantment or self.is_aura:
            return
        if self.etb_effect or self.static_effect:
            return  # Already has an effect
        
        # ── WHENEVER triggers ──
        if 'whenever' in text:
            if 'draw' in text:
                def ench_draw(game, card):
                    card.controller.draw_card(1, game=game)
                    game.log_event(f"Enchantment: {card.name} — draw a card")
                self.enchantment_trigger = ench_draw
                return
            
            dmg = re.search(r'deals? (\d+) damage', text)
            if dmg:
                amt = int(dmg.group(1))
                def ench_dmg(game, card, amount=amt):
                    opp = game.players[(game.players.index(card.controller) + 1) % 2]
                    opp.life -= amount
                    game.log_event(f"Enchantment: {card.name} deals {amount}")
                self.enchantment_trigger = ench_dmg
                return
            
            if 'gain' in text and 'life' in text:
                life = re.search(r'gain (\d+) life', text)
                amt = int(life.group(1)) if life else 1
                def ench_life(game, card, amount=amt):
                    card.controller.life += amount
                    game.log_event(f"Enchantment: {card.name} gains {amount} life")
                self.enchantment_trigger = ench_life
                return
            
            tok = re.search(r'create.*?(\d+)/(\d+)', text)
            if tok:
                tp, tt = int(tok.group(1)), int(tok.group(2))
                def ench_token(game, card, _tp=tp, _tt=tt):
                    from engine.card import Card
                    token = Card(name="Token", cost="", type_line="Token Creature",
                                base_power=_tp, base_toughness=_tt, oracle_text="")
                    token.controller = card.controller
                    token.summoning_sickness = True
                    game.battlefield.add(token)
                    game.log_event(f"Enchantment: {card.name} creates {_tp}/{_tt} token")
                self.enchantment_trigger = ench_token
                return
            
            if '+1/+1 counter' in text:
                def ench_counter(game, card):
                    my_creatures = [c for c in game.battlefield.cards 
                                   if c.controller == card.controller and c.is_creature]
                    if my_creatures:
                        best = max(my_creatures, key=lambda c: (c.power or 0))
                        best.counters['+1/+1'] = best.counters.get('+1/+1', 0) + 1
                        game.log_event(f"Enchantment: {card.name} adds +1/+1 to {best.name}")
                self.enchantment_trigger = ench_counter
                return
            
            if 'destroy' in text or 'exile' in text:
                def ench_removal(game, card):
                    opp = game.players[(game.players.index(card.controller) + 1) % 2]
                    targets = [c for c in game.battlefield.cards if c.controller == opp and c.is_creature
                              and not c.has_hexproof]
                    if targets:
                        t = min(targets, key=lambda c: (c.power or 0))
                        game.battlefield.remove(t)
                        if not t.is_token: opp.graveyard.add(t)
                        game.log_event(f"Enchantment: {card.name} removes {t.name}")
                self.enchantment_trigger = ench_removal
                self.is_removal = True
                return
            
            # Generic 'whenever' with lose life
            lose_life = re.search(r'loses? (\d+) life', text)
            if lose_life:
                amt = int(lose_life.group(1))
                def ench_drain(game, card, amount=amt):
                    opp = game.players[(game.players.index(card.controller) + 1) % 2]
                    opp.life -= amount
                    game.log_event(f"Enchantment: {card.name} — {opp.name} loses {amount} life")
                self.enchantment_trigger = ench_drain
                return
        
        # ── AT THE BEGINNING triggers (upkeep/end step) ──
        if 'at the beginning' in text:
            if 'draw' in text:
                def ench_begin_draw(game, card):
                    card.controller.draw_card(1, game=game)
                    game.log_event(f"Enchantment upkeep: {card.name} — draw")
                self.upkeep_effect = ench_begin_draw
                return
            
            dmg = re.search(r'deals? (\d+) damage|loses? (\d+) life', text)
            if dmg:
                amt = int(dmg.group(1) or dmg.group(2))
                def ench_begin_dmg(game, card, amount=amt):
                    opp = game.players[(game.players.index(card.controller) + 1) % 2]
                    opp.life -= amount
                    game.log_event(f"Enchantment upkeep: {card.name} — {opp.name} loses {amount}")
                self.upkeep_effect = ench_begin_dmg
                return
            
            if 'gain' in text and 'life' in text:
                life = re.search(r'gain (\d+) life', text)
                amt = int(life.group(1)) if life else 1
                def ench_begin_life(game, card, amount=amt):
                    card.controller.life += amount
                    game.log_event(f"Enchantment upkeep: {card.name} gains {amount} life")
                self.upkeep_effect = ench_begin_life
                return
            
            tok = re.search(r'create.*?(\d+)/(\d+)', text)
            if tok:
                tp, tt = int(tok.group(1)), int(tok.group(2))
                def ench_begin_token(game, card, _tp=tp, _tt=tt):
                    from engine.card import Card
                    token = Card(name="Token", cost="", type_line="Token Creature",
                                base_power=_tp, base_toughness=_tt, oracle_text="")
                    token.controller = card.controller
                    token.summoning_sickness = True
                    game.battlefield.add(token)
                    game.log_event(f"Enchantment upkeep: {card.name} creates {_tp}/{_tt}")
                self.upkeep_effect = ench_begin_token
                return
            
            if '+1/+1 counter' in text:
                def ench_begin_counter(game, card):
                    my_creatures = [c for c in game.battlefield.cards 
                                   if c.controller == card.controller and c.is_creature]
                    if my_creatures:
                        best = max(my_creatures, key=lambda c: (c.power or 0))
                        best.counters['+1/+1'] = best.counters.get('+1/+1', 0) + 1
                        game.log_event(f"Enchantment upkeep: {card.name} adds +1/+1 to {best.name}")
                self.upkeep_effect = ench_begin_counter
                return
            
            if 'sacrifice' in text:
                def ench_begin_sac(game, card):
                    opp = game.players[(game.players.index(card.controller) + 1) % 2]
                    opp_creatures = [c for c in game.battlefield.cards if c.controller == opp and c.is_creature]
                    if opp_creatures:
                        weakest = min(opp_creatures, key=lambda c: (c.power or 0))
                        game.battlefield.remove(weakest)
                        if not weakest.is_token: opp.graveyard.add(weakest)
                        game.log_event(f"Enchantment upkeep: {card.name} — {opp.name} sacs {weakest.name}")
                self.upkeep_effect = ench_begin_sac
                return
            
            mill_match = re.search(r'mills? (\d+)', text)
            if mill_match:
                amt = int(mill_match.group(1))
                def ench_begin_mill(game, card, amount=amt):
                    opp = game.players[(game.players.index(card.controller) + 1) % 2]
                    for _ in range(amount):
                        if opp.library.cards:
                            m = opp.library.draw()
                            if m: opp.graveyard.add(m)
                    game.log_event(f"Enchantment upkeep: {card.name} mills {amount}")
                self.upkeep_effect = ench_begin_mill
                return

    def _parse_protection(self, text: str):
        """Parse 'protection from' keyword (Rule 702.16)."""
        if 'protection from' not in text:
            return
        self.has_protection = True
        # Populate has_protection_from list for is_protected_from() checks
        prot_match = re.findall(r'protection from (\w+)', text)
        for p in prot_match:
            if p in ('white', 'blue', 'black', 'red', 'green',
                     'creatures', 'instants', 'sorceries', 'everything'):
                if p not in self.has_protection_from:
                    self.has_protection_from.append(p)

    def _parse_self_pump(self, text: str):
        """Parse creatures with conditional self-buffs like 'gets +N/+N as long as'."""
        if not self.is_creature:
            return
        if self.static_effect or self.attack_trigger:
            return
        
        # "gets +N/+N as long as" / "has flying as long as" / "gets +N/+N for each"
        buff = re.search(r'gets? \+(\d+)/\+(\d+)', text)
        if buff:
            self.has_self_pump = True
            self.self_pump_power = max(self.self_pump_power, int(buff.group(1)))
            self.self_pump_toughness = max(self.self_pump_toughness, int(buff.group(2)))
            return
        
        # "gets -N/-N" (self-debuff or conditional debuff)
        debuff = re.search(r'gets? -(\d+)/-(\d+)', text)
        if debuff:
            self.has_self_pump = True
            return

    def _parse_sacrifice_ability(self, text: str):
        """Parse 'sacrifice ~' or 'sacrifice a creature' activated abilities."""
        if not self.is_creature:
            return
        if self.death_effect or self.attack_trigger:
            return
        if 'sacrifice' not in text:
            return
        
        # "sacrifice ~: draw" 
        if 'draw' in text:
            def sac_draw(game, card):
                game.battlefield.remove(card)
                if not card.is_token: card.controller.graveyard.add(card)
                card.controller.draw_card(1, game=game)
                game.log_event(f"Sacrifice: {card.name} — draw a card")
            self.sacrifice_effect = sac_draw
            return
        
        # "sacrifice ~: deal N damage"
        dmg = re.search(r'sacrifice.*deals? (\d+) damage', text)
        if dmg:
            amt = int(dmg.group(1))
            def sac_dmg(game, card, amount=amt):
                game.battlefield.remove(card)
                if not card.is_token: card.controller.graveyard.add(card)
                opp = game.players[(game.players.index(card.controller) + 1) % 2]
                opp.life -= amount
                game.log_event(f"Sacrifice: {card.name} deals {amount}")
            self.sacrifice_effect = sac_dmg
            return
        
        # "sacrifice ~: +1/+1 counter"
        if '+1/+1 counter' in text:
            def sac_counter(game, card):
                game.battlefield.remove(card)
                if not card.is_token: card.controller.graveyard.add(card)
                my_creatures = [c for c in game.battlefield.cards 
                               if c.controller == card.controller and c.is_creature]
                if my_creatures:
                    best = max(my_creatures, key=lambda c: (c.power or 0))
                    best.counters['+1/+1'] = best.counters.get('+1/+1', 0) + 1
                    game.log_event(f"Sacrifice: {card.name} — +1/+1 to {best.name}")
            self.sacrifice_effect = sac_counter
            return
        
        # "sacrifice ~: create token"
        tok = re.search(r'sacrifice.*create.*?(\d+)/(\d+)', text)
        if tok:
            tp, tt = int(tok.group(1)), int(tok.group(2))
            def sac_token(game, card, _tp=tp, _tt=tt):
                game.battlefield.remove(card)
                if not card.is_token: card.controller.graveyard.add(card)
                from engine.card import Card
                token = Card(name="Token", cost="", type_line="Token Creature",
                            base_power=_tp, base_toughness=_tt, oracle_text="")
                token.controller = card.controller
                token.summoning_sickness = True
                game.battlefield.add(token)
                game.log_event(f"Sacrifice: {card.name} creates {_tp}/{_tt}")
            self.sacrifice_effect = sac_token
            return
        
        # "sacrifice ~: destroy"
        if 'destroy' in text:
            def sac_destroy(game, card):
                game.battlefield.remove(card)
                if not card.is_token: card.controller.graveyard.add(card)
                opp = game.players[(game.players.index(card.controller) + 1) % 2]
                targets = [c for c in game.battlefield.cards if c.controller == opp and c.is_creature
                          and not c.has_hexproof and not c.has_indestructible]
                if targets:
                    t = max(targets, key=lambda c: (c.power or 0))
                    game.battlefield.remove(t)
                    if not t.is_token: opp.graveyard.add(t)
                    game.log_event(f"Sacrifice: {card.name} destroys {t.name}")
            self.sacrifice_effect = sac_destroy
            self.is_removal = True
            return
        
        # Generic sacrifice: gain life or return
        if 'gain' in text and 'life' in text:
            life = re.search(r'gain (\d+) life', text)
            amt = int(life.group(1)) if life else 2
            def sac_life(game, card, amount=amt):
                game.battlefield.remove(card)
                if not card.is_token: card.controller.graveyard.add(card)
                card.controller.life += amount
                game.log_event(f"Sacrifice: {card.name} — gain {amount} life")
            self.sacrifice_effect = sac_life
            return

    def _parse_creature_restriction(self, text: str):
        """Parse creatures with restrictions (can't, don't, only, must)."""
        if not self.is_creature:
            return
        if self.attack_trigger or self.death_effect or self.etb_effect:
            return
        if re.search(r"can't be blocked\b", text):
            self.has_restriction = True  # evasion
            return
        if "can't block" in text:
            self.has_restriction = True  # aggro-only
            return
        if "can't attack" in text:
            self.has_restriction = True  # defender-like
            return
        if "doesn't untap" in text:
            self.has_restriction = True
            return
        if "must attack" in text or "must be blocked" in text:
            self.has_restriction = True
            return

    def _parse_artifact_tap(self, text: str):
        """Parse artifact tap abilities ({T}: effect)."""
        if not (self.is_artifact and not self.is_creature):
            return
        if getattr(self, 'equip_bonus', None) or self.is_mana_dork:
            return
        if '{t}:' not in text and '{t},' not in text:
            return
        if getattr(self, 'activated_abilities', None):
            return
        
        tap_text = text.split('{t}')[1] if '{t}' in text else ''
        
        # Damage
        dmg = re.search(r'deals? (\d+) damage', tap_text)
        if dmg:
            amt = int(dmg.group(1))
            def art_tap_dmg(game, card, amount=amt):
                if card.tapped: return
                card.tapped = True
                opp = game.players[(game.players.index(card.controller) + 1) % 2]
                opp.life -= amount
                game.log_event(f"Artifact tap: {card.name} deals {amount}")
            self.tap_ability_effect = art_tap_dmg
            return
        
        # Draw
        if 'draw' in tap_text:
            def art_tap_draw(game, card):
                if card.tapped: return
                card.tapped = True
                card.controller.draw_card(1, game=game)
                game.log_event(f"Artifact tap: {card.name} — draw")
            self.tap_ability_effect = art_tap_draw
            return
        
        # Add mana
        if re.search(r'add\s', tap_text):
            self.is_mana_dork = True  # Treat as mana source
            return
        
        # Gain life
        life = re.search(r'gain (\d+) life', tap_text)
        if life:
            amt = int(life.group(1))
            def art_tap_life(game, card, amount=amt):
                if card.tapped: return
                card.tapped = True
                card.controller.life += amount
                game.log_event(f"Artifact tap: {card.name} gains {amount} life")
            self.tap_ability_effect = art_tap_life
            return
        
        # Counter
        if '+1/+1 counter' in tap_text:
            def art_tap_counter(game, card):
                if card.tapped: return
                card.tapped = True
                my_creatures = [c for c in game.battlefield.cards 
                               if c.controller == card.controller and c.is_creature]
                if my_creatures:
                    best = max(my_creatures, key=lambda c: (c.power or 0))
                    best.counters['+1/+1'] = best.counters.get('+1/+1', 0) + 1
                    game.log_event(f"Artifact tap: {card.name} adds +1/+1 to {best.name}")
            self.tap_ability_effect = art_tap_counter
            return
        
        # Tap target creature
        if 'tap target' in tap_text:
            def art_tap_target(game, card):
                if card.tapped: return
                card.tapped = True
                opp = game.players[(game.players.index(card.controller) + 1) % 2]
                targets = [c for c in game.battlefield.cards if c.controller == opp 
                          and c.is_creature and not c.tapped]
                if targets:
                    t = max(targets, key=lambda c: (c.power or 0))
                    t.tapped = True
                    game.log_event(f"Artifact tap: {card.name} taps {t.name}")
            self.tap_ability_effect = art_tap_target
            return

    def _parse_broad_etb(self, text: str):
        """Broad catch-all for 'enters' text on creatures without ETB already parsed."""
        if not self.is_creature:
            return
        if self.etb_effect or self.attack_trigger or self.death_effect:
            return
        if 'enters' not in text:
            return
        
        # Draw on enter
        if 'draw' in text and 'enters' in text:
            self.etb_effect = self._make_draw_effect(1)
            return
        
        # Damage on enter
        dmg = re.search(r'enters.*deals? (\d+) damage', text)
        if dmg:
            amt = int(dmg.group(1))
            self.etb_effect = self._make_damage_effect(amt, 'any')
            return
        
        # Life gain on enter
        life = re.search(r'enters.*gain (\d+) life', text)
        if life:
            amt = int(life.group(1))
            self.etb_effect = self._make_lifegain_effect(amt)
            return
        
        # +1/+1 counter on enter
        if 'enters' in text and '+1/+1 counter' in text:
            def broad_etb_counter(game, card):
                card.counters['+1/+1'] = card.counters.get('+1/+1', 0) + 1
                game.log_event(f"ETB: {card.name} gets +1/+1 counter")
            self.etb_effect = broad_etb_counter
            return
        
        # Token on enter
        tok = re.search(r'enters.*create.*?(\d+)/(\d+)', text)
        if tok:
            tp, tt = int(tok.group(1)), int(tok.group(2))
            def broad_etb_token(game, card, _tp=tp, _tt=tt):
                from engine.card import Card
                token = Card(name="Token", cost="", type_line="Token Creature",
                            base_power=_tp, base_toughness=_tt, oracle_text="")
                token.controller = card.controller
                token.summoning_sickness = True
                game.battlefield.add(token)
                game.log_event(f"ETB: {card.name} creates {_tp}/{_tt} token")
            self.etb_effect = broad_etb_token
            return
        
        # Exile on enter
        if 'enters' in text and 'exile' in text:
            self.etb_effect = self._make_exile_effect()
            return
        
        # Destroy on enter
        if 'enters' in text and 'destroy' in text:
            self.etb_effect = self._make_destroy_permanent_effect()
            return

    def _parse_broad_whenever(self, text: str):
        """Broad catch-all for remaining 'whenever' triggers on creatures."""
        if not self.is_creature:
            return
        if self.attack_trigger or self.death_effect or self.etb_effect:
            return
        if getattr(self, 'cast_trigger', None) or getattr(self, 'block_trigger', None):
            return
        if 'whenever' not in text:
            return
        
        # Draw
        if 'draw' in text:
            def broad_draw(game, card):
                card.controller.draw_card(1, game=game)
                game.log_event(f"Trigger: {card.name} — draw a card")
            self.broad_trigger = broad_draw
            return
        
        # Damage
        dmg = re.search(r'deals? (\d+) damage', text)
        if dmg:
            amt = int(dmg.group(1))
            def broad_dmg(game, card, amount=amt):
                opp = game.players[(game.players.index(card.controller) + 1) % 2]
                opp.life -= amount
                game.log_event(f"Trigger: {card.name} deals {amount}")
            self.broad_trigger = broad_dmg
            return
        
        # Life gain
        life = re.search(r'gain (\d+) life', text)
        if life:
            amt = int(life.group(1))
            def broad_life(game, card, amount=amt):
                card.controller.life += amount
                game.log_event(f"Trigger: {card.name} gains {amount} life")
            self.broad_trigger = broad_life
            return
        
        # +1/+1 counter
        if '+1/+1 counter' in text:
            def broad_counter(game, card):
                card.counters['+1/+1'] = card.counters.get('+1/+1', 0) + 1
                game.log_event(f"Trigger: {card.name} gets +1/+1 counter")
            self.broad_trigger = broad_counter
            return
        
        # Token creation
        tok = re.search(r'create.*?(\d+)/(\d+)', text)
        if tok:
            tp, tt = int(tok.group(1)), int(tok.group(2))
            def broad_token(game, card, _tp=tp, _tt=tt):
                from engine.card import Card
                token = Card(name="Token", cost="", type_line="Token Creature",
                            base_power=_tp, base_toughness=_tt, oracle_text="")
                token.controller = card.controller
                token.summoning_sickness = True
                game.battlefield.add(token)
                game.log_event(f"Trigger: {card.name} creates {_tp}/{_tt} token")
            self.broad_trigger = broad_token
            return
        
        # Lose life
        lose = re.search(r'loses? (\d+) life', text)
        if lose:
            amt = int(lose.group(1))
            def broad_drain(game, card, amount=amt):
                opp = game.players[(game.players.index(card.controller) + 1) % 2]
                opp.life -= amount
                game.log_event(f"Trigger: {card.name} — opponent loses {amount} life")
            self.broad_trigger = broad_drain
            return
        
        # Exile
        if 'exile' in text:
            def broad_exile(game, card):
                opp = game.players[(game.players.index(card.controller) + 1) % 2]
                targets = [c for c in game.battlefield.cards if c.controller == opp and c.is_creature
                          and not c.has_hexproof]
                if targets:
                    t = min(targets, key=lambda c: (c.power or 0) + (c.toughness or 0))
                    game.battlefield.remove(t)
                    opp.exile.add(t)
                    game.log_event(f"Trigger: {card.name} exiles {t.name}")
            self.broad_trigger = broad_exile
            return
        
        # Destroy
        if 'destroy' in text:
            def broad_destroy(game, card):
                opp = game.players[(game.players.index(card.controller) + 1) % 2]
                targets = [c for c in game.battlefield.cards if c.controller == opp and c.is_creature
                          and not c.has_hexproof and not c.has_indestructible]
                if targets:
                    t = max(targets, key=lambda c: (c.power or 0))
                    game.battlefield.remove(t)
                    if not t.is_token: opp.graveyard.add(t)
                    game.log_event(f"Trigger: {card.name} destroys {t.name}")
            self.broad_trigger = broad_destroy
            self.is_removal = True
            return
        
        # Mill
        mill = re.search(r'mills? (\d+)', text)
        if mill:
            amt = int(mill.group(1))
            def broad_mill(game, card, amount=amt):
                opp = game.players[(game.players.index(card.controller) + 1) % 2]
                for _ in range(amount):
                    if opp.library.cards:
                        m = opp.library.draw()
                        if m: opp.graveyard.add(m)
                game.log_event(f"Trigger: {card.name} mills {amount}")
            self.broad_trigger = broad_mill
            return
        
        # Discard
        if 'discard' in text:
            def broad_discard(game, card):
                opp = game.players[(game.players.index(card.controller) + 1) % 2]
                if opp.hand.cards:
                    worst = min(opp.hand.cards, key=lambda c: (c.power or 0) + (c.toughness or 0))
                    opp.hand.remove(worst)
                    opp.graveyard.add(worst)
                    game.log_event(f"Trigger: {card.name} — {opp.name} discards {worst.name}")
            self.broad_trigger = broad_discard
            return

    def _parse_broad_spell(self, text: str):
        """Broad catch-all for instant/sorcery effects not caught by earlier parsers."""
        if not (self.is_instant or self.is_sorcery):
            return
        # Skip if already has an effect
        if self.effect or self.is_removal or self.is_burn or self.is_counter or \
           self.is_board_wipe or self.is_buff or self.is_draw or self.is_lifegain or \
           self.is_discard or self.is_mill:
            return
        
        # 1. Damage/burn
        dmg = re.search(r'deals? (\d+) damage', text)
        if dmg:
            amt = int(dmg.group(1))
            def spell_dmg(game, card, amount=amt):
                opp = game.players[(game.players.index(card.controller) + 1) % 2]
                opp.life -= amount
                game.log_event(f"Spell: {card.name} deals {amount} damage")
            self.effect = spell_dmg
            self.is_burn = True
            return
        
        # 2. Removal — destroy/exile target creature/permanent
        if re.search(r'(destroy|exile)\s+(target|all|each)\s+(creature|permanent|nonland|artifact|enchantment)', text):
            if 'all' in text or 'each' in text:
                self.is_board_wipe = True
            def spell_remove(game, card):
                opp = game.players[(game.players.index(card.controller) + 1) % 2]
                targets = [c for c in game.battlefield.cards if c.controller == opp
                          and c.is_creature and not c.has_hexproof and not c.has_indestructible]
                if targets:
                    t = max(targets, key=lambda c: (c.power or 0) + (c.toughness or 0))
                    game.battlefield.remove(t)
                    if 'exile' in text:
                        opp.exile.add(t)
                    elif not t.is_token:
                        opp.graveyard.add(t)
                    game.log_event(f"Spell: {card.name} removes {t.name}")
            self.effect = spell_remove
            self.is_removal = True
            return
        
        # 3. Counterspell
        if re.search(r'counter target (spell|ability)', text):
            self.is_counter = True
            return
        
        # 4. Draw
        draw_match = re.search(r'draw\s+(\w+)\s+cards?', text)
        if draw_match or 'draw a card' in text:
            if draw_match:
                word = draw_match.group(1)
                word_to_num = {'a': 1, 'one': 1, 'two': 2, 'three': 3, 'four': 4, 'five': 5}
                try: amt = int(word)
                except ValueError: amt = word_to_num.get(word, 1)
            else:
                amt = 1
            def spell_draw(game, card, amount=amt):
                card.controller.draw_card(amount, game=game)
                game.log_event(f"Spell: {card.name} — draw {amount}")
            self.effect = spell_draw
            self.is_draw = True
            return
        
        # 5. Bounce/return
        if re.search(r'return\s+(target|all|each)\s+\w+\s+to\s+(its|their)\s+owner', text):
            def spell_bounce(game, card):
                opp = game.players[(game.players.index(card.controller) + 1) % 2]
                targets = [c for c in game.battlefield.cards if c.controller == opp and c.is_creature]
                if targets:
                    t = max(targets, key=lambda c: (c.power or 0))
                    game.battlefield.remove(t)
                    if not t.is_token: opp.hand.add(t)
                    game.log_event(f"Spell: {card.name} bounces {t.name}")
            self.effect = spell_bounce
            self.is_removal = True
            return
        
        # 6. Token creation
        tok = re.search(r'create\s+(\w+)\s+(\d+)/(\d+)', text)
        if tok:
            num_word = tok.group(1)
            word_to_num = {'a': 1, 'one': 1, 'two': 2, 'three': 3, 'four': 4, 'five': 5}
            try: num = int(num_word)
            except ValueError: num = word_to_num.get(num_word, 1)
            tp, tt = int(tok.group(2)), int(tok.group(3))
            def spell_token(game, card, _n=num, _tp=tp, _tt=tt):
                from engine.card import Card
                for _ in range(_n):
                    token = Card(name="Token", cost="", type_line="Token Creature",
                                base_power=_tp, base_toughness=_tt, oracle_text="")
                    token.controller = card.controller
                    token.summoning_sickness = True
                    game.battlefield.add(token)
                game.log_event(f"Spell: {card.name} creates {_n}x {_tp}/{_tt} tokens")
            self.effect = spell_token
            return
        
        # 7. Exile (broader)
        if re.search(r'exile\s+(target|a|up to)', text):
            def spell_exile(game, card):
                opp = game.players[(game.players.index(card.controller) + 1) % 2]
                targets = [c for c in game.battlefield.cards if c.controller == opp
                          and not c.has_hexproof]
                if targets:
                    t = max(targets, key=lambda c: (c.power or 0) + (c.toughness or 0))
                    game.battlefield.remove(t)
                    opp.exile.add(t)
                    game.log_event(f"Spell: {card.name} exiles {t.name}")
            self.effect = spell_exile
            self.is_removal = True
            return
        
        # 8. Life gain
        life = re.search(r'gain (\d+) life', text)
        if life:
            amt = int(life.group(1))
            def spell_life(game, card, amount=amt):
                card.controller.life += amount
                game.log_event(f"Spell: {card.name} gains {amount} life")
            self.effect = spell_life
            self.is_lifegain = True
            return
        
        # 9. Discard — broader patterns to catch Thoughtseize, Inquisition, etc.
        if (re.search(r'(target|each|that)\s+(player|opponent)\s+discards?', text) or 
            'discard a card' in text or
            ('reveal' in text and 'hand' in text and 'discard' in text) or
            ('choose' in text and 'discard' in text)):
            def spell_discard(game, card):
                opp = game.players[(game.players.index(card.controller) + 1) % 2]
                if opp.hand.cards:
                    worst = min(opp.hand.cards, key=lambda c: (c.power or 0) + (c.toughness or 0))
                    opp.hand.remove(worst)
                    opp.graveyard.add(worst)
                    game.log_event(f"Spell: {card.name} — {opp.name} discards {worst.name}")
            self.effect = spell_discard
            self.is_discard = True
            return
        
        # 10. Tap/untap
        if re.search(r'tap\s+(target|all|each|up to)', text):
            def spell_tap(game, card):
                opp = game.players[(game.players.index(card.controller) + 1) % 2]
                targets = [c for c in game.battlefield.cards if c.controller == opp
                          and c.is_creature and not c.tapped]
                if targets:
                    t = max(targets, key=lambda c: (c.power or 0))
                    t.tapped = True
                    game.log_event(f"Spell: {card.name} taps {t.name}")
            self.effect = spell_tap
            return
        
        # 11. Debuff (-N/-N)
        debuff = re.search(r'gets? -(\d+)/-(\d+)', text)
        if debuff:
            dp, dt = int(debuff.group(1)), int(debuff.group(2))
            def spell_debuff(game, card, _dp=dp, _dt=dt):
                opp = game.players[(game.players.index(card.controller) + 1) % 2]
                targets = [c for c in game.battlefield.cards if c.controller == opp
                          and c.is_creature and not c.has_hexproof]
                if targets:
                    t = max(targets, key=lambda c: (c.power or 0))
                    if not hasattr(t, '_temp_modifiers'): t._temp_modifiers = []
                    t._temp_modifiers.append({'power': -_dp, 'toughness': -_dt})
                    game.log_event(f"Spell: {card.name} gives -{_dp}/-{_dt} to {t.name}")
            self.effect = spell_debuff
            self.is_removal = True
            return
        
        # 12. Search library / tutor
        if 'search' in text and ('library' in text or 'deck' in text):
            def spell_search(game, card):
                player = card.controller
                if player.library.cards:
                    player.draw_card(1, game=game)
                    game.log_event(f"Spell: {card.name} — search (approximated as draw)")
            self.effect = spell_search
            self.is_draw = True
            return
        
        # 13. Sacrifice
        if re.search(r'sacrifice\s+(a|target|all)', text):
            def spell_sac(game, card):
                opp = game.players[(game.players.index(card.controller) + 1) % 2]
                targets = [c for c in game.battlefield.cards if c.controller == opp and c.is_creature]
                if targets:
                    t = min(targets, key=lambda c: (c.power or 0))
                    game.battlefield.remove(t)
                    if not t.is_token: opp.graveyard.add(t)
                    game.log_event(f"Spell: {card.name} — {opp.name} sacrifices {t.name}")
            self.effect = spell_sac
            self.is_removal = True
            return
        
        # 14. Mill
        mill = re.search(r'mills?\s+(\d+)', text)
        if mill:
            amt = int(mill.group(1))
            def spell_mill(game, card, amount=amt):
                opp = game.players[(game.players.index(card.controller) + 1) % 2]
                for _ in range(amount):
                    if opp.library.cards:
                        m = opp.library.draw()
                        if m: opp.graveyard.add(m)
                game.log_event(f"Spell: {card.name} mills {amount}")
            self.effect = spell_mill
            self.is_mill = True
            return
        
        # 15. Buff (+N/+N)
        buff = re.search(r'gets? \+(\d+)/\+(\d+)', text)
        if buff:
            bp, bt = int(buff.group(1)), int(buff.group(2))
            def spell_buff(game, card, _bp=bp, _bt=bt):
                my_creatures = [c for c in game.battlefield.cards 
                               if c.controller == card.controller and c.is_creature]
                if my_creatures:
                    best = max(my_creatures, key=lambda c: (c.power or 0))
                    if not hasattr(best, '_temp_modifiers'): best._temp_modifiers = []
                    best._temp_modifiers.append({'power': _bp, 'toughness': _bt})
                    game.log_event(f"Spell: {card.name} gives +{_bp}/+{_bt} to {best.name}")
            self.effect = spell_buff
            self.is_buff = True
            return
        
        # 16. Lose life
        lose = re.search(r'loses? (\d+) life', text)
        if lose:
            amt = int(lose.group(1))
            def spell_drain(game, card, amount=amt):
                opp = game.players[(game.players.index(card.controller) + 1) % 2]
                opp.life -= amount
                game.log_event(f"Spell: {card.name} — {opp.name} loses {amount} life")
            self.effect = spell_drain
            self.is_burn = True
            return

    def _parse_creature_upkeep(self, text: str):
        """Parse creatures with 'at the beginning of your upkeep/end step' triggers."""
        if not self.is_creature:
            return
        if self.upkeep_effect or self.attack_trigger or self.etb_effect or self.death_effect:
            return
        if 'at the beginning' not in text:
            return
        
        # Draw
        if 'draw' in text:
            def cre_up_draw(game, card):
                card.controller.draw_card(1, game=game)
                game.log_event(f"Upkeep: {card.name} — draw")
            self.upkeep_effect = cre_up_draw
            return
        
        # Damage/lose life
        dmg = re.search(r'deals? (\d+) damage|loses? (\d+) life', text)
        if dmg:
            amt = int(dmg.group(1) or dmg.group(2))
            def cre_up_dmg(game, card, amount=amt):
                opp = game.players[(game.players.index(card.controller) + 1) % 2]
                opp.life -= amount
                game.log_event(f"Upkeep: {card.name} — {opp.name} loses {amount}")
            self.upkeep_effect = cre_up_dmg
            return
        
        # Counter
        if '+1/+1 counter' in text:
            def cre_up_counter(game, card):
                card.counters['+1/+1'] = card.counters.get('+1/+1', 0) + 1
                game.log_event(f"Upkeep: {card.name} gets +1/+1 counter")
            self.upkeep_effect = cre_up_counter
            return
        
        # Token
        tok = re.search(r'create.*?(\d+)/(\d+)', text)
        if tok:
            tp, tt = int(tok.group(1)), int(tok.group(2))
            def cre_up_token(game, card, _tp=tp, _tt=tt):
                from engine.card import Card
                token = Card(name="Token", cost="", type_line="Token Creature",
                            base_power=_tp, base_toughness=_tt, oracle_text="")
                token.controller = card.controller
                token.summoning_sickness = True
                game.battlefield.add(token)
                game.log_event(f"Upkeep: {card.name} creates {_tp}/{_tt} token")
            self.upkeep_effect = cre_up_token
            return
        
        # Mill
        mill = re.search(r'mills? (\d+)', text)
        if mill:
            amt = int(mill.group(1))
            def cre_up_mill(game, card, amount=amt):
                opp = game.players[(game.players.index(card.controller) + 1) % 2]
                for _ in range(amount):
                    if opp.library.cards:
                        m = opp.library.draw()
                        if m: opp.graveyard.add(m)
                game.log_event(f"Upkeep: {card.name} mills {amount}")
            self.upkeep_effect = cre_up_mill
            return
        
        # Sacrifice
        if 'sacrifice' in text:
            def cre_up_sac(game, card):
                opp = game.players[(game.players.index(card.controller) + 1) % 2]
                targets = [c for c in game.battlefield.cards if c.controller == opp and c.is_creature]
                if targets:
                    weakest = min(targets, key=lambda c: (c.power or 0))
                    game.battlefield.remove(weakest)
                    if not weakest.is_token: opp.graveyard.add(weakest)
                    game.log_event(f"Upkeep: {card.name} — {opp.name} sacrifices {weakest.name}")
            self.upkeep_effect = cre_up_sac
            return
        
        # Gain life
        life = re.search(r'gain (\d+) life', text)
        if life:
            amt = int(life.group(1))
            def cre_up_life(game, card, amount=amt):
                card.controller.life += amount
                game.log_event(f"Upkeep: {card.name} gains {amount} life")
            self.upkeep_effect = cre_up_life
            return


    def _parse_activated_ability(self, text: str):
        """Parse creatures with non-tap activated abilities ({cost}: effect)."""
        if not self.is_creature:
            return
        if self.tap_ability_effect or self.sacrifice_effect or self.etb_effect:
            return
        if self.attack_trigger or self.death_effect or getattr(self, 'broad_trigger', None):
            return
        if self.is_mana_dork:
            return
        
        # Match {cost}: pattern but exclude {T}: (already handled)
        if re.search(r'{[^t}][^}]*}:', text) or re.search(r'{[^}]+}{[^}]+}:', text):
            self.has_activated_ability = True

    def _parse_broad_enchantment(self, text: str):
        """Broad catch-all for enchantments not yet parsed."""
        if not self.is_enchantment:
            return
        if self.is_aura or self.static_effect or self.etb_effect or self.upkeep_effect:
            return
        if getattr(self, 'enchantment_trigger', None):
            return
        
        # Enchantments with 'whenever' we missed (diverse conditions)
        if 'whenever' in text:
            if 'damage' in text or 'lose' in text:
                def ench_broad_dmg(game, card):
                    opp = game.players[(game.players.index(card.controller) + 1) % 2]
                    opp.life -= 1
                    game.log_event(f"Enchantment: {card.name} triggers")
                self.enchantment_trigger = ench_broad_dmg
                return
            if 'draw' in text:
                def ench_broad_draw(game, card):
                    card.controller.draw_card(1, game=game)
                    game.log_event(f"Enchantment: {card.name} — draw")
                self.enchantment_trigger = ench_broad_draw
                return
            # Generic trigger
            def ench_broad_generic(game, card):
                game.log_event(f"Enchantment: {card.name} triggers")
            self.enchantment_trigger = ench_broad_generic
            return
        
        # 'At the beginning' triggers
        if 'at the beginning' in text:
            if 'draw' in text:
                def ench_broad_begin_draw(game, card):
                    card.controller.draw_card(1, game=game)
                    game.log_event(f"Enchantment upkeep: {card.name} — draw")
                self.upkeep_effect = ench_broad_begin_draw
                return
            if 'damage' in text or 'lose' in text:
                def ench_broad_begin_dmg(game, card):
                    opp = game.players[(game.players.index(card.controller) + 1) % 2]
                    opp.life -= 1
                    game.log_event(f"Enchantment upkeep: {card.name} triggers")
                self.upkeep_effect = ench_broad_begin_dmg
                return
            if 'counter' in text or 'token' in text:
                def ench_broad_begin_gen(game, card):
                    game.log_event(f"Enchantment upkeep: {card.name} triggers")
                self.upkeep_effect = ench_broad_begin_gen
                return
        
        # Enchantments with static text that affects gameplay
        if re.search(r"can't|don't|doesn't|each|all|your|opponent", text):
            # Flag as having a static effect if it has restriction-like text
            self.static_effect = lambda game, card: None  # Placeholder
            return
        
        # Enchanted creature — missed aura
        if 'enchanted creature' in text:
            self.is_aura = True
            return
        
        # Activated ability on enchantment
        if re.search(r'{[^}]*}:', text):
            self.has_activated_ability = True
            return
        
        # Sacrifice text
        if 'sacrifice' in text:
            self.has_text_ability = True
            return
        
        # Mentions creature/target/you/your
        if re.search(r'creature|target|you |your |player', text):
            self.has_text_ability = True
            return

    def _parse_creature_text_fallback(self, text: str):
        """Final fallback: any creature with meaningful text gets has_text_ability=True."""
        if not self.is_creature:
            return
        if not text.strip():
            return
        # Skip if already captured by any effect
        if (self.effect or self.etb_effect or self.death_effect or 
            self.attack_trigger or self.upkeep_effect or self.landfall_effect or
            self.combat_damage_trigger or self.static_effect or
            getattr(self, 'cast_trigger', None) or getattr(self, 'block_trigger', None) or
            self.is_mana_dork or self.tap_ability_effect or
            self.has_protection or self.has_self_pump or
            self.sacrifice_effect or self.has_restriction or
            getattr(self, 'broad_trigger', None) or
            self.has_activated_ability or
            self.has_flying or self.has_trample or self.has_lifelink or
            self.has_deathtouch or self.has_first_strike or self.has_double_strike or
            self.has_vigilance or self.has_reach or self.has_flash or
            self.has_hexproof or self.has_menace or self.has_indestructible or
            self.has_defender or self.has_haste or self.has_ward or
            self.has_prowess):
            return
        
        # If it has ANY game text, flag it — coverage catch-all
        self.has_text_ability = True

    def _parse_spell_target_fallback(self, text: str):
        """Final fallback: spells with 'target' or 'each/all' that weren't caught get an effect."""
        if not (self.is_instant or self.is_sorcery):
            return
        if self.effect or self.is_removal or self.is_burn or self.is_counter or \
           self.is_board_wipe or self.is_buff or self.is_draw or self.is_lifegain or \
           self.is_discard or self.is_mill:
            return
        if not text.strip():
            return
        
        # Spells with 'target' are usually single-target effects
        if 'target' in text:
            if 'creature' in text and ('destroy' in text or 'exile' in text or 'return' in text or 'bounce' in text or '-' in text):
                self.is_removal = True
                return
            # Discard BEFORE burn — Thoughtseize has 'player' + 'lose' but is discard
            if 'discard' in text:
                self.is_discard = True
                def spell_discard_fb(game, card):
                    opp = game.players[(game.players.index(card.controller) + 1) % 2]
                    if opp.hand.cards:
                        worst = min(opp.hand.cards, key=lambda c: (c.power or 0) + (c.toughness or 0))
                        opp.hand.remove(worst)
                        opp.graveyard.add(worst)
                        game.log_event(f"Spell: {card.name} — {opp.name} discards {worst.name}")
                self.effect = spell_discard_fb
                return
            if 'player' in text and ('damage' in text or 'lose' in text):
                self.is_burn = True
                return
            # Generic targeted spell
            def spell_target_fallback(game, card):
                game.log_event(f"Spell: {card.name} resolves (targeted, approximated)")
            self.effect = spell_target_fallback
            return
        
        # Spells with 'each/all' are usually mass effects
        if re.search(r'each|all creatures|all nonland|all permanent', text):
            if 'destroy' in text or 'exile' in text or '-' in text:
                self.is_board_wipe = True
                return
            if 'damage' in text:
                self.is_burn = True
                return
            # Generic mass spell
            def spell_each_fallback(game, card):
                game.log_event(f"Spell: {card.name} resolves (mass, approximated)")
            self.effect = spell_each_fallback
            return
        
        # Damage spells we missed
        if 'damage' in text:
            self.is_burn = True
            return
        
        # Destroy/exile spells
        if 'destroy' in text or 'exile' in text:
            self.is_removal = True
            return
        
        # Graveyard interaction
        if 'graveyard' in text or 'return' in text:
            def spell_graveyard_fallback(game, card):
                game.log_event(f"Spell: {card.name} resolves (graveyard, approximated)")
            self.effect = spell_graveyard_fallback
            return
        
        # Land/mana spells
        if 'land' in text or 'mana' in text:
            def spell_land_fallback(game, card):
                game.log_event(f"Spell: {card.name} resolves (land/mana, approximated)")
            self.effect = spell_land_fallback
            return
        
        # Temporary buffs
        if re.search(r'until end of turn|gets? [+-]', text):
            self.is_buff = True
            return
        
        # Token creation
        if 'token' in text or 'create' in text:
            def spell_token_fallback(game, card):
                game.log_event(f"Spell: {card.name} resolves (token, approximated)")
            self.effect = spell_token_fallback
            return
        
        # Discard effects
        if 'discard' in text:
            self.is_discard = True
            return
        
        # Any remaining spell with meaningful text
        if text.strip():
            def spell_generic_fallback(game, card):
                game.log_event(f"Spell: {card.name} resolves (approximated)")
            self.effect = spell_generic_fallback
            return

    def _parse_artifact_other(self, text: str):
        """Catch-all for non-creature artifacts with text but no parsed effect."""
        if not (self.is_artifact and not self.is_creature):
            return
        if not text.strip():
            return
        if (self.tap_ability_effect or self.static_effect or self.upkeep_effect or
            self.is_mana_dork or getattr(self, 'equip_bonus', None) or
            getattr(self, 'activated_abilities', None) or self.etb_effect):
            return
        
        # Any non-creature artifact with text gets flagged
        self.has_text_ability = True

    def _parse_morph(self, text: str):
        """Parse morph/megamorph/disguise keywords."""
        if not self.is_creature:
            return
        if re.search(r'morph|megamorph|disguise|manifest|cloak', text):
            self.has_morph = True
    
    def _parse_planeswalker_fallback(self, text: str):
        """Catch-all for planeswalkers without parsed effects."""
        if not self.is_planeswalker:
            return
        if self.effect or self.etb_effect or getattr(self, 'activated_abilities', None):
            return
        if not text.strip():
            return
        # Any planeswalker with text has abilities
        self.has_text_ability = True

    def _parse_broad_text_abilities(self, text: str):
        """Catch top-frequency oracle text patterns that fall through existing parsers.
        
        This runs after all specialized parsers but before the final catch-all.
        Covers: tap abilities on non-creatures, broader ETB, tokens, bounce,
        equipment bonuses, P/T modification.
        """
        if self.is_land:
            return
        # Skip if already has a parsed effect
        if (self.effect or self.etb_effect or self.death_effect or 
            self.attack_trigger or self.upkeep_effect or self.landfall_effect or
            self.combat_damage_trigger or getattr(self, 'tap_ability_effect', None) or
            getattr(self, 'sacrifice_effect', None) or
            getattr(self, 'enchantment_trigger', None) or
            self.static_effect or getattr(self, 'equip_bonus', None) or
            getattr(self, 'cast_trigger', None) or getattr(self, 'block_trigger', None)):
            return
        
        oracle = text.strip()
        if not oracle:
            return
        
        # 1. TAP ABILITIES ON NON-CREATURES (artifacts, enchantments, planeswalkers)
        if '{t}:' in oracle or '{t},' in oracle:
            if not self.is_creature or self.is_mana_dork:
                # This is a non-creature with a tap ability
                tap_text = oracle.split('{t}')[1] if '{t}' in oracle else ''
                
                # Draw
                if 'draw' in tap_text:
                    def tap_draw_nc(game, card):
                        if card.tapped: return
                        card.tapped = True
                        card.controller.draw_card(1, game=game)
                        game.log_event(f"Tap: {card.name} — draw a card")
                    self.tap_ability_effect = tap_draw_nc
                    self.has_activated_ability = True
                    return
                
                # Damage
                dmg_match = re.search(r'deals? (\d+) damage', tap_text)
                if dmg_match:
                    amt = int(dmg_match.group(1))
                    def tap_dmg_nc(game, card, amount=amt):
                        if card.tapped: return
                        card.tapped = True
                        opp = game.players[(game.players.index(card.controller) + 1) % 2]
                        opp.life -= amount
                        game.log_event(f"Tap: {card.name} deals {amount} damage")
                    self.tap_ability_effect = tap_dmg_nc
                    self.has_activated_ability = True
                    return
                
                # Life gain
                life_match = re.search(r'gain (\d+) life', tap_text)
                if life_match:
                    amt = int(life_match.group(1))
                    def tap_life_nc(game, card, amount=amt):
                        if card.tapped: return
                        card.tapped = True
                        card.controller.life += amount
                        game.log_event(f"Tap: {card.name} — gain {amount} life")
                    self.tap_ability_effect = tap_life_nc
                    self.has_activated_ability = True
                    return
                
                # Token creation
                tok_match = re.search(r'create.*?(\d+)/(\d+)', tap_text)
                if tok_match:
                    tp, tt = int(tok_match.group(1)), int(tok_match.group(2))
                    def tap_token_nc(game, card, _tp=tp, _tt=tt):
                        if card.tapped: return
                        card.tapped = True
                        token = Card(name="Token", cost="", type_line="Token Creature",
                                    base_power=_tp, base_toughness=_tt, oracle_text="")
                        token.controller = card.controller
                        token.summoning_sickness = True
                        game.battlefield.add(token)
                        game.log_event(f"Tap: {card.name} creates {_tp}/{_tt} token")
                    self.tap_ability_effect = tap_token_nc
                    self.has_activated_ability = True
                    return
                
                # Mill
                mill_match = re.search(r'mills? (\d+)', tap_text)
                if mill_match:
                    amt = int(mill_match.group(1))
                    def tap_mill_nc(game, card, amount=amt):
                        if card.tapped: return
                        card.tapped = True
                        opp = game.players[(game.players.index(card.controller) + 1) % 2]
                        for _ in range(amount):
                            if opp.library.cards:
                                m = opp.library.draw()
                                if m: opp.graveyard.add(m)
                        game.log_event(f"Tap: {card.name} mills {amount}")
                    self.tap_ability_effect = tap_mill_nc
                    self.has_activated_ability = True
                    return
                
                # Destroy/Exile
                if 'destroy' in tap_text or 'exile' in tap_text:
                    is_exile = 'exile' in tap_text
                    def tap_remove_nc(game, card, _exile=is_exile):
                        if card.tapped: return
                        card.tapped = True
                        opp = game.players[(game.players.index(card.controller) + 1) % 2]
                        targets = [c for c in game.battlefield.cards 
                                  if c.controller == opp and c.is_creature and not c.has_hexproof]
                        if targets:
                            t = max(targets, key=lambda c: (c.power or 0))
                            game.battlefield.remove(t)
                            if _exile:
                                opp.exile.add(t)
                                game.log_event(f"Tap: {card.name} exiles {t.name}")
                            else:
                                if not t.is_token: opp.graveyard.add(t)
                                game.log_event(f"Tap: {card.name} destroys {t.name}")
                    self.tap_ability_effect = tap_remove_nc
                    self.has_activated_ability = True
                    self.is_removal = True
                    return
                
                # Scry
                if 'scry' in tap_text:
                    def tap_scry_nc(game, card):
                        if card.tapped: return
                        card.tapped = True
                        game.log_event(f"Tap: {card.name} — scry")
                    self.tap_ability_effect = tap_scry_nc
                    self.has_activated_ability = True
                    return
                
                # Generic tap ability — mark as activated
                self.has_activated_ability = True
                self.has_text_ability = True
                return
        
        # 2. BROADER ETB TRIGGERS  
        etb_patterns = [
            r'when\s+\w+\s+enters',
            r'when\s+this\s+creature\s+enters',
            r'whenever\s+a\s+creature\s+enters',
            r'whenever\s+a\s+\w+\s+enters\s+the\s+battlefield',
            r'when\s+\w+\s+enters\s+the\s+battlefield',
        ]
        if not self.etb_effect:
            for pat in etb_patterns:
                if re.search(pat, oracle):
                    # Parse effect type
                    if 'draw' in oracle:
                        def etb_draw(game, card):
                            card.controller.draw_card(1, game=game)
                            game.log_event(f"ETB: {card.name} — draw a card")
                        self.etb_effect = etb_draw
                        self.is_draw = True
                    elif re.search(r'deals?\s+(\d+)\s+damage', oracle):
                        dmg = int(re.search(r'deals?\s+(\d+)\s+damage', oracle).group(1))
                        def etb_damage(game, card, _dmg=dmg):
                            opp = game.players[(game.players.index(card.controller) + 1) % 2]
                            opp.life -= _dmg
                            game.log_event(f"ETB: {card.name} deals {_dmg} damage")
                        self.etb_effect = etb_damage
                        self.is_burn = True
                    elif re.search(r'create.*?(\d+)/(\d+)', oracle):
                        m = re.search(r'create.*?(\d+)/(\d+)', oracle)
                        tp, tt = int(m.group(1)), int(m.group(2))
                        def etb_token(game, card, _tp=tp, _tt=tt):
                            token = Card(name="Token", cost="", type_line="Token Creature",
                                        base_power=_tp, base_toughness=_tt, oracle_text="")
                            token.controller = card.controller
                            token.summoning_sickness = True
                            game.battlefield.add(token)
                            game.log_event(f"ETB: {card.name} creates {_tp}/{_tt} token")
                        self.etb_effect = etb_token
                    elif 'destroy' in oracle or 'exile' in oracle:
                        is_exile = 'exile' in oracle
                        def etb_remove(game, card, _exile=is_exile):
                            opp = game.players[(game.players.index(card.controller) + 1) % 2]
                            targets = [c for c in game.battlefield.cards 
                                      if c.controller == opp and c.is_creature
                                      and not c.has_hexproof]
                            if targets:
                                t = max(targets, key=lambda c: (c.power or 0))
                                game.battlefield.remove(t)
                                if _exile:
                                    opp.exile.add(t)
                                    game.log_event(f"ETB: {card.name} exiles {t.name}")
                                else:
                                    if not t.is_token: opp.graveyard.add(t)
                                    game.log_event(f"ETB: {card.name} destroys {t.name}")
                        self.etb_effect = etb_remove
                        self.is_removal = True
                    elif re.search(r'gain\s+(\d+)\s+life', oracle):
                        amt = int(re.search(r'gain\s+(\d+)\s+life', oracle).group(1))
                        def etb_life(game, card, _amt=amt):
                            card.controller.life += _amt
                            game.log_event(f"ETB: {card.name} — gain {_amt} life")
                        self.etb_effect = etb_life
                        self.is_lifegain = True
                    elif '+1/+1 counter' in oracle:
                        def etb_counter(game, card):
                            card.counters['+1/+1'] = card.counters.get('+1/+1', 0) + 1
                            game.log_event(f"ETB: {card.name} gets +1/+1 counter")
                        self.etb_effect = etb_counter
                    elif 'return' in oracle and ('graveyard' in oracle or 'hand' in oracle):
                        def etb_bounce(game, card):
                            game.log_event(f"ETB: {card.name} — return effect")
                        self.etb_effect = etb_bounce
                    else:
                        def etb_generic(game, card):
                            game.log_event(f"ETB: {card.name} triggers")
                        self.etb_effect = etb_generic
                    break
        
        # 3. EQUIPMENT BONUSES
        if self.is_equipment and not getattr(self, 'equip_bonus', None):
            equipped_match = re.search(r'equipped creature gets? ([+-]\d+)/([+-]\d+)', oracle)
            if equipped_match:
                ep, et = int(equipped_match.group(1)), int(equipped_match.group(2))
                self.equip_bonus = {'power': ep, 'toughness': et}
                # Check for keyword grants
                if 'flying' in oracle: self.equip_bonus['flying'] = True
                if 'trample' in oracle: self.equip_bonus['trample'] = True
                if 'first strike' in oracle: self.equip_bonus['first_strike'] = True
                if 'deathtouch' in oracle: self.equip_bonus['deathtouch'] = True
                if 'lifelink' in oracle: self.equip_bonus['lifelink'] = True
                if 'vigilance' in oracle: self.equip_bonus['vigilance'] = True
                if 'haste' in oracle: self.equip_bonus['haste'] = True
                return
        
        # 4. DEATH TRIGGERS (broader catch)
        if not self.death_effect:
            death_patterns = [r'when\s+\w+\s+dies', r'whenever\s+\w+\s+dies']
            for pat in death_patterns:
                if re.search(pat, oracle):
                    if 'draw' in oracle:
                        def death_draw(game, card):
                            card.controller.draw_card(1, game=game)
                            game.log_event(f"Death: {card.name} — draw a card")
                        self.death_effect = death_draw
                    elif re.search(r'deals?\s+(\d+)\s+damage', oracle):
                        dmg = int(re.search(r'deals?\s+(\d+)\s+damage', oracle).group(1))
                        def death_dmg(game, card, _dmg=dmg):
                            opp = game.players[(game.players.index(card.controller) + 1) % 2]
                            opp.life -= _dmg
                            game.log_event(f"Death: {card.name} deals {_dmg} damage")
                        self.death_effect = death_dmg
                    elif re.search(r'create.*?(\d+)/(\d+)', oracle):
                        m = re.search(r'create.*?(\d+)/(\d+)', oracle)
                        tp, tt = int(m.group(1)), int(m.group(2))
                        def death_token(game, card, _tp=tp, _tt=tt):
                            token = Card(name="Token", cost="", type_line="Token Creature",
                                        base_power=_tp, base_toughness=_tt, oracle_text="")
                            token.controller = card.controller
                            token.summoning_sickness = True
                            game.battlefield.add(token)
                            game.log_event(f"Death: {card.name} creates {_tp}/{_tt} token")
                        self.death_effect = death_token
                    else:
                        def death_generic(game, card):
                            game.log_event(f"Death: {card.name} — trigger")
                        self.death_effect = death_generic
                    break
        
        # 5. CAST TRIGGERS (broader catch)
        if not getattr(self, 'cast_trigger', None):
            if re.search(r'when(ever)?\s+you\s+cast', oracle):
                if 'draw' in oracle:
                    def cast_draw(game, card):
                        card.controller.draw_card(1, game=game)
                        game.log_event(f"Cast trigger: {card.name} — draw")
                    self.cast_trigger = cast_draw
                elif re.search(r'deals?\s+(\d+)\s+damage', oracle):
                    dmg = int(re.search(r'deals?\s+(\d+)\s+damage', oracle).group(1))
                    def cast_dmg(game, card, _dmg=dmg):
                        opp = game.players[(game.players.index(card.controller) + 1) % 2]
                        opp.life -= _dmg
                        game.log_event(f"Cast trigger: {card.name} deals {_dmg}")
                    self.cast_trigger = cast_dmg
                else:
                    def cast_generic(game, card):
                        game.log_event(f"Cast trigger: {card.name}")
                    self.cast_trigger = cast_generic
        
        # 6. WHENEVER-TRIGGERS (broader catch — any "whenever" not yet handled)
        if re.search(r'whenever\s+', oracle) and not self.etb_effect and not self.death_effect:
            # Parse the trigger condition and effect
            if re.search(r'whenever.*attacks', oracle) and not self.attack_trigger:
                if 'draw' in oracle:
                    def atk_draw(game, card):
                        card.controller.draw_card(1, game=game)
                        game.log_event(f"Attack trigger: {card.name} — draw")
                    self.attack_trigger = atk_draw
                elif re.search(r'deals?\s+(\d+)\s+damage', oracle):
                    dmg = int(re.search(r'deals?\s+(\d+)\s+damage', oracle).group(1))
                    def atk_dmg(game, card, _d=dmg):
                        opp = game.players[(game.players.index(card.controller) + 1) % 2]
                        opp.life -= _d
                        game.log_event(f"Attack trigger: {card.name} deals {_d}")
                    self.attack_trigger = atk_dmg
                elif re.search(r'create.*?(\d+)/(\d+)', oracle):
                    m = re.search(r'create.*?(\d+)/(\d+)', oracle)
                    tp, tt = int(m.group(1)), int(m.group(2))
                    def atk_token(game, card, _tp=tp, _tt=tt):
                        token = Card(name="Token", cost="", type_line="Token Creature",
                                    base_power=_tp, base_toughness=_tt, oracle_text="")
                        token.controller = card.controller
                        token.summoning_sickness = True
                        game.battlefield.add(token)
                        game.log_event(f"Attack trigger: {card.name} creates {_tp}/{_tt}")
                    self.attack_trigger = atk_token
                else:
                    def atk_generic(game, card):
                        game.log_event(f"Attack trigger: {card.name}")
                    self.attack_trigger = atk_generic
                return
            
            # Whenever a creature dies — not our card's death, but any creature
            if re.search(r'whenever.*dies', oracle):
                # This is an "aristocrats" style trigger
                if 'draw' in oracle:
                    def aristo_draw(game, card):
                        card.controller.draw_card(1, game=game)
                        game.log_event(f"Whenever-dies: {card.name} — draw")
                    self.enchantment_trigger = aristo_draw
                elif re.search(r'gain\s+(\d+)\s+life', oracle):
                    amt = int(re.search(r'gain\s+(\d+)\s+life', oracle).group(1))
                    def aristo_life(game, card, _a=amt):
                        card.controller.life += _a
                        game.log_event(f"Whenever-dies: {card.name} — gain {_a} life")
                    self.enchantment_trigger = aristo_life
                else:
                    def aristo_generic(game, card):
                        game.log_event(f"Whenever-dies: {card.name}")
                    self.enchantment_trigger = aristo_generic
                return
            
            # Whenever you cast a spell
            if re.search(r'whenever\s+you\s+cast', oracle):
                if 'draw' in oracle:
                    def wcast_draw(game, card):
                        card.controller.draw_card(1, game=game)
                        game.log_event(f"Whenever-cast: {card.name} — draw")
                    self.enchantment_trigger = wcast_draw
                else:
                    def wcast_generic(game, card):
                        game.log_event(f"Whenever-cast: {card.name}")
                    self.enchantment_trigger = wcast_generic
                return
            
            # Whenever a creature enters the battlefield (under your control)
            if re.search(r'whenever\s+a\s+(creature|nontoken)', oracle):
                if 'draw' in oracle:
                    def wcreature_draw(game, card):
                        card.controller.draw_card(1, game=game)
                        game.log_event(f"Whenever-creature: {card.name} — draw")
                    self.enchantment_trigger = wcreature_draw
                elif re.search(r'gain\s+(\d+)\s+life', oracle):
                    amt = int(re.search(r'gain\s+(\d+)\s+life', oracle).group(1))
                    def wcreature_life(game, card, _a=amt):
                        card.controller.life += _a
                        game.log_event(f"Whenever-creature: {card.name} — gain {_a} life")
                    self.enchantment_trigger = wcreature_life
                else:
                    def wcreature_generic(game, card):
                        game.log_event(f"Whenever-creature: {card.name}")
                    self.enchantment_trigger = wcreature_generic
                return
            
            # Generic whenever — mark as having a trigger effect
            if not self.enchantment_trigger:
                def whenever_generic(game, card):
                    game.log_event(f"Whenever: {card.name} triggers")
                self.enchantment_trigger = whenever_generic
                return
        
        # 7. SACRIFICE-AS-EFFECT (not sacrifice-as-cost)
        if 'sacrifice' in oracle and not getattr(self, 'sacrifice_effect', None):
            if re.search(r'sacrifice\s+(a|another)\s+creature', oracle):
                if 'draw' in oracle:
                    def sac_draw(game, card):
                        own = [c for c in game.battlefield.cards 
                              if c.controller == card.controller and c.is_creature and c is not card]
                        if own:
                            weakest = min(own, key=lambda c: (c.power or 0))
                            game.battlefield.remove(weakest)
                            if not weakest.is_token: card.controller.graveyard.add(weakest)
                            card.controller.draw_card(1, game=game)
                            game.log_event(f"Sacrifice: {card.name} sacs {weakest.name}, draws")
                    self.sacrifice_effect = sac_draw
                    return
                elif re.search(r'deals?\s+(\d+)\s+damage', oracle):
                    dmg = int(re.search(r'deals?\s+(\d+)\s+damage', oracle).group(1))
                    def sac_dmg(game, card, _d=dmg):
                        own = [c for c in game.battlefield.cards 
                              if c.controller == card.controller and c.is_creature and c is not card]
                        if own:
                            weakest = min(own, key=lambda c: (c.power or 0))
                            game.battlefield.remove(weakest)
                            if not weakest.is_token: card.controller.graveyard.add(weakest)
                            opp = game.players[(game.players.index(card.controller) + 1) % 2]
                            opp.life -= _d
                            game.log_event(f"Sacrifice: {card.name} sacs {weakest.name}, deals {_d}")
                    self.sacrifice_effect = sac_dmg
                    return
                else:
                    def sac_generic(game, card):
                        game.log_event(f"Sacrifice effect: {card.name}")
                    self.sacrifice_effect = sac_generic
                    return
        
        # 8. UPKEEP/STEP TRIGGERS
        if re.search(r'at the beginning of', oracle) and not self.upkeep_effect:
            if 'draw' in oracle:
                def upkeep_draw(game, card):
                    card.controller.draw_card(1, game=game)
                    game.log_event(f"Upkeep: {card.name} — draw")
                self.upkeep_effect = upkeep_draw
                return
            elif re.search(r'deals?\s+(\d+)\s+damage', oracle):
                dmg = int(re.search(r'deals?\s+(\d+)\s+damage', oracle).group(1))
                def upkeep_dmg(game, card, _d=dmg):
                    opp = game.players[(game.players.index(card.controller) + 1) % 2]
                    opp.life -= _d
                    game.log_event(f"Upkeep: {card.name} deals {_d}")
                self.upkeep_effect = upkeep_dmg
                return
            elif re.search(r'gain\s+(\d+)\s+life', oracle):
                amt = int(re.search(r'gain\s+(\d+)\s+life', oracle).group(1))
                def upkeep_life(game, card, _a=amt):
                    card.controller.life += _a
                    game.log_event(f"Upkeep: {card.name} — gain {_a} life")
                self.upkeep_effect = upkeep_life
                return
            elif re.search(r'create.*?(\d+)/(\d+)', oracle):
                m = re.search(r'create.*?(\d+)/(\d+)', oracle)
                tp, tt = int(m.group(1)), int(m.group(2))
                def upkeep_token(game, card, _tp=tp, _tt=tt):
                    token = Card(name="Token", cost="", type_line="Token Creature",
                                base_power=_tp, base_toughness=_tt, oracle_text="")
                    token.controller = card.controller
                    token.summoning_sickness = True
                    game.battlefield.add(token)
                    game.log_event(f"Upkeep: {card.name} creates {_tp}/{_tt}")
                self.upkeep_effect = upkeep_token
                return
            elif re.search(r'lose\s+(\d+)\s+life', oracle):
                amt = int(re.search(r'lose\s+(\d+)\s+life', oracle).group(1))
                def upkeep_lose(game, card, _a=amt):
                    card.controller.life -= _a
                    game.log_event(f"Upkeep: {card.name} — lose {_a} life")
                self.upkeep_effect = upkeep_lose
                self.has_drawback = True
                return
            else:
                def upkeep_generic(game, card):
                    game.log_event(f"Upkeep: {card.name} triggers")
                self.upkeep_effect = upkeep_generic
                return
        
        # 9. ACTIVATED ABILITIES WITH MANA COSTS ({N}: effect or {color}: effect)
        mana_ability_match = re.search(r'\{([0-9wubrgx]+)\}:', oracle)
        if mana_ability_match and not getattr(self, 'has_activated_ability', False):
            ability_text = oracle.split(mana_ability_match.group(0))[1] if mana_ability_match.group(0) in oracle else ''
            if 'draw' in ability_text:
                def mana_draw(game, card):
                    card.controller.draw_card(1, game=game)
                    game.log_event(f"Ability: {card.name} — draw")
                self.tap_ability_effect = mana_draw
                self.has_activated_ability = True
                return
            elif re.search(r'deals?\s+(\d+)\s+damage', ability_text):
                dmg = int(re.search(r'deals?\s+(\d+)\s+damage', ability_text).group(1))
                def mana_dmg(game, card, _d=dmg):
                    opp = game.players[(game.players.index(card.controller) + 1) % 2]
                    opp.life -= _d
                    game.log_event(f"Ability: {card.name} deals {_d}")
                self.tap_ability_effect = mana_dmg
                self.has_activated_ability = True
                return
            elif re.search(r'gain\s+(\d+)\s+life', ability_text):
                amt = int(re.search(r'gain\s+(\d+)\s+life', ability_text).group(1))
                def mana_life(game, card, _a=amt):
                    card.controller.life += _a
                    game.log_event(f"Ability: {card.name} — gain {_a} life")
                self.tap_ability_effect = mana_life
                self.has_activated_ability = True
                return
            else:
                self.has_activated_ability = True
                return
        
        # 10. P/T MODIFICATION (static/aura)
        pt_match = re.search(r'gets?\s+([+-]\d+)/([+-]\d+)', oracle)
        if pt_match and not self.static_effect:
            ep, et = int(pt_match.group(1)), int(pt_match.group(2))
            if self.is_enchantment or self.is_artifact:
                self.static_effect = {
                    'filter': 'other_creatures' if 'other' in oracle else 'all_creatures',
                    'power': ep, 'toughness': et
                }
                return
        
        # 11. COST MODIFIERS — "spells cost {1} more/less to cast"
        cost_mod = re.search(r'spells?\s+(you\s+cast\s+)?cost\s+\{(\d+)\}\s+(more|less)\s+to\s+cast', oracle)
        if cost_mod and not self.static_effect:
            self.static_effect = {
                'type': 'cost_modifier',
                'amount': int(cost_mod.group(2)),
                'direction': cost_mod.group(3),
                'filter': 'you' if cost_mod.group(1) else 'all'
            }
            return
        
        # 12. KEYWORD GRANTS — "creatures you control have X"
        keyword_grant = re.search(r'creatures?\s+(?:you\s+control\s+)?have\s+(flying|double\s+strike|first\s+strike|trample|lifelink|deathtouch|vigilance|haste|menace|hexproof|indestructible)', oracle)
        if keyword_grant and not self.static_effect:
            kw = keyword_grant.group(1).replace(' ', '_')
            self.static_effect = {
                'type': 'keyword_grant',
                'keyword': kw,
                'filter': 'your_creatures'
            }
            return
        
        # 13. MORPH / MEGAMORPH / DISGUISE
        if re.search(r'(morph|megamorph|disguise)\s+\{', oracle):
            self.has_activated_ability = True
            return
        
        # 14. INFECT / WITHER / TOXIC
        if re.search(r'\b(infect|wither)\b', oracle):
            self.has_infect = True
            return
        if re.search(r'toxic\s+(\d+)', oracle):
            toxic_match = re.search(r'toxic\s+(\d+)', oracle)
            self.has_toxic = int(toxic_match.group(1)) if toxic_match else 1
            return
        
        # 15. STATIC RESTRICTIONS — "can't/cannot" effects
        if ("can't" in oracle or 'cannot' in oracle) and not self.static_effect:
            self.static_effect = {
                'type': 'restriction',
                'text': oracle[:80]
            }
            return
        
        # 16. "EACH" / "ALL" global effects
        if re.search(r'(each|all)\s+(creature|player|opponent)', oracle) and not self.static_effect:
            if re.search(r'deals?\s+(\d+)\s+damage', oracle):
                dmg = int(re.search(r'deals?\s+(\d+)\s+damage', oracle).group(1))
                def each_dmg(game, card, _d=dmg):
                    opp = game.players[(game.players.index(card.controller) + 1) % 2]
                    opp.life -= _d
                    game.log_event(f"Each: {card.name} deals {_d} to opponent")
                self.enchantment_trigger = each_dmg
                return
            elif 'lose' in oracle and 'life' in oracle:
                lose_match = re.search(r'loses?\s+(\d+)\s+life', oracle)
                if lose_match:
                    amt = int(lose_match.group(1))
                    def each_lose(game, card, _a=amt):
                        opp = game.players[(game.players.index(card.controller) + 1) % 2]
                        opp.life -= _a
                        game.log_event(f"Each: {card.name} — opponent loses {_a}")
                    self.enchantment_trigger = each_lose
                    return
            self.static_effect = {'type': 'global_effect', 'text': oracle[:80]}
            return
        
        # 17. CONVOKE / ASSIST / ADDITIONAL COSTS
        if re.search(r'\b(convoke|assist|emerge|improvise|delve)\b', oracle):
            self.has_activated_ability = True
            return
        
        # 18. "RETURN" effects (bounce/reanimate not caught above)
        if 'return' in oracle and not self.etb_effect:
            if 'graveyard' in oracle:
                def return_from_gy(game, card):
                    game.log_event(f"Effect: {card.name} — return from graveyard")
                self.etb_effect = return_from_gy
                return
            elif 'hand' in oracle:
                def return_to_hand(game, card):
                    game.log_event(f"Effect: {card.name} — bounce to hand")
                self.etb_effect = return_to_hand
                return

    def _parse_remaining_text(self, text: str):
        """Second sweep: catches cards that fell through all parsers.
        
        Runs after _parse_broad_text_abilities but before _parse_final_coverage.
        Focuses on creatures with recognizable but previously-unhandled patterns.
        """
        if self.is_land:
            return
        # Skip if already has a parsed effect
        if (self.effect or self.etb_effect or self.death_effect or 
            self.attack_trigger or self.upkeep_effect or self.landfall_effect or
            self.combat_damage_trigger or getattr(self, 'tap_ability_effect', None) or
            getattr(self, 'sacrifice_effect', None) or
            getattr(self, 'enchantment_trigger', None) or
            self.static_effect or getattr(self, 'equip_bonus', None) or
            getattr(self, 'cast_trigger', None) or getattr(self, 'block_trigger', None) or
            self.has_activated_ability or self.broad_trigger or 
            self.has_restriction or self.has_self_pump or
            getattr(self, 'has_infect', False) or getattr(self, 'has_toxic', False)):
            return
        
        oracle = text.strip()
        if not oracle:
            return
        
        # 1. Sacrifice this creature: <effect> — activated abilities
        sac_match = re.search(r'sacrifice\s+(?:this|~).*?:\s*(.+)', oracle, re.IGNORECASE)
        if sac_match and self.is_creature:
            effect_text = sac_match.group(1).lower()
            if 'exile' in effect_text:
                def sac_exile(game, card):
                    game.battlefield.remove(card)
                    if not card.is_token: card.controller.graveyard.add(card)
                    opp = game.players[(game.players.index(card.controller) + 1) % 2]
                    targets = [c for c in game.battlefield.cards if c.controller == opp and c.is_creature]
                    if targets:
                        t = max(targets, key=lambda c: (c.power or 0))
                        game.battlefield.remove(t); opp.exile.add(t)
                    game.log_event(f"Sacrifice: {card.name} — exile effect")
                self.sacrifice_effect = sac_exile
                self.is_removal = True
                return
            elif 'draw' in effect_text:
                def sac_draw(game, card):
                    game.battlefield.remove(card)
                    if not card.is_token: card.controller.graveyard.add(card)
                    card.controller.draw_card(1, game=game)
                    game.log_event(f"Sacrifice: {card.name} — draw")
                self.sacrifice_effect = sac_draw
                return
            elif 'damage' in effect_text or 'lose' in effect_text:
                dmg_match = re.search(r'(\d+)\s+(?:damage|life)', effect_text)
                amt = int(dmg_match.group(1)) if dmg_match else 2
                def sac_dmg(game, card, _a=amt):
                    game.battlefield.remove(card)
                    if not card.is_token: card.controller.graveyard.add(card)
                    opp = game.players[(game.players.index(card.controller) + 1) % 2]
                    opp.life -= _a
                    game.log_event(f"Sacrifice: {card.name} — {_a} damage")
                self.sacrifice_effect = sac_dmg
                return
            else:
                # Generic sacrifice-for-effect
                def sac_generic(game, card):
                    game.battlefield.remove(card)
                    if not card.is_token: card.controller.graveyard.add(card)
                    game.log_event(f"Sacrifice: {card.name} — effect")
                self.sacrifice_effect = sac_generic
                return
        
        # 2. "attacks each combat if able" — forced attacker
        if re.search(r'attacks?\s+each\s+(?:combat|turn)\s+if\s+able', oracle):
            self.has_restriction = True
            return
        
        # 3. CDA P/T — "power and toughness are each equal to"
        if re.search(r"power\s+and\s+toughness\s+are\s+(?:each\s+)?equal\s+to", oracle):
            if 'graveyard' in oracle:
                self.cda_type = 'nighthowler'
            elif 'land' in oracle:
                self.cda_type = 'tarmogoyf'  # Use as approximation
            elif 'life' in oracle:
                self.cda_type = 'serra_avatar'
            elif 'card' in oracle:
                self.cda_type = 'nighthowler'
            else:
                self.cda_type = 'tarmogoyf'
            return
        
        # 4. Discard: "discard a card:" activated ability
        if re.search(r'discard\s+a\s+card\s*:', oracle) and self.is_creature:
            def discard_ability(game, card):
                if card.controller.hand.count > 0:
                    card.controller.discard(1, game=game)
                    game.log_event(f"Ability: {card.name} — discard for effect")
            self.has_activated_ability = True
            self.sacrifice_effect = discard_ability
            return
        
        # 5. When this creature becomes the target — trigger
        if re.search(r'when\s+(?:this|~)\s+becomes?\s+(?:the\s+)?target', oracle):
            self.broad_trigger = True
            return
        
        # 6. Regenerate
        if 'regenerate' in oracle:
            self.has_activated_ability = True
            return
        
        # 7. Soulbond / pair / partner
        if re.search(r'\b(soulbond|partner|pair)\b', oracle):
            self.has_activated_ability = True
            return
        
        # 8. Planeswalker loyalty abilities
        if self.is_planeswalker:
            self.has_activated_ability = True
            return
        
        # 9. Additional costs ("as an additional cost")
        if 'as an additional cost' in oracle:
            self.has_drawback = True
            return
        
        # 10. "When this creature dies" not caught by death trigger parser
        if re.search(r'when\s+(?:this|~)\s+(?:creature\s+)?dies', oracle) and not self.death_effect:
            if 'draw' in oracle:
                def death_draw(game, card):
                    card.controller.draw_card(1, game=game)
                    game.log_event(f"Death: {card.name} — draw")
                self.death_effect = death_draw
                return
            elif 'damage' in oracle:
                dmg_match = re.search(r'(\d+)\s+damage', oracle)
                amt = int(dmg_match.group(1)) if dmg_match else 1
                def death_dmg(game, card, _a=amt):
                    opp = game.players[(game.players.index(card.controller) + 1) % 2]
                    opp.life -= _a
                    game.log_event(f"Death: {card.name} — {_a} damage")
                self.death_effect = death_dmg
                return
            elif 'token' in oracle:
                tok_match = re.search(r'(\d+)/(\d+)', oracle)
                tp = int(tok_match.group(1)) if tok_match else 1
                tt = int(tok_match.group(2)) if tok_match else 1
                def death_token(game, card, _tp=tp, _tt=tt):
                    token = Card(name="Token", cost="", type_line="Token Creature",
                                base_power=_tp, base_toughness=_tt, oracle_text="")
                    token.controller = card.controller
                    token.summoning_sickness = True
                    game.battlefield.add(token)
                    game.log_event(f"Death: {card.name} — create token")
                self.death_effect = death_token
                return
            else:
                self.broad_trigger = True
                return
        
        # 11. "When this creature deals damage" not caught by combat_damage_trigger
        if re.search(r'when(?:ever)?\s+(?:this|~)\s+(?:creature\s+)?deals?\s+(?:combat\s+)?damage', oracle):
            self.broad_trigger = True
            return
        
        # 12. "Whenever" triggers not caught above
        if 'whenever' in oracle and self.is_creature:
            self.broad_trigger = True
            return
        
        # 13. "Enchant" / aura-like effects on non-aura cards
        if re.search(r'enchanted?\s+(?:creature|permanent|land)', oracle):
            self.has_activated_ability = True
            return
        
        # 14. Protection from / hexproof from
        if re.search(r'(?:protection|hexproof)\s+from', oracle):
            self.has_restriction = True
            return
        
        # 15. "{number}: effect" — generic activated abilities
        if re.search(r'\{\d+\}\s*:', oracle):
            self.has_activated_ability = True
            return
        
        # 16. "Target" effects on any card
        if 'target' in oracle and (self.is_creature or self.is_artifact or self.is_enchantment):
            self.has_activated_ability = True
            return

    def _parse_final_coverage(self, text: str):
        """Final catch-all: ensure every non-land card is 'covered' by at least one flag.
        
        Handles:
        1. Vanilla creatures (no text) → is_vanilla = True
        2. DFC/split cards (// in name) → has_text_ability = True
        3. Static enchantments (cost mods, damage prevention) → static_effect
        4. Any remaining card with oracle text → has_text_ability = True
        """
        if self.is_land:
            return
            
        # Check if card already has ANY coverage flag
        if (self.effect or self.etb_effect or self.death_effect or 
            self.attack_trigger or self.upkeep_effect or self.landfall_effect or
            self.combat_damage_trigger or getattr(self, 'activated_abilities', None) or
            self.static_effect or getattr(self, 'equip_bonus', None) or
            getattr(self, 'cast_trigger', None) or getattr(self, 'block_trigger', None) or
            getattr(self, 'is_mana_dork', False) or
            getattr(self, 'tap_ability_effect', None) or
            getattr(self, 'enchantment_trigger', None) or
            getattr(self, 'has_protection', False) or
            getattr(self, 'has_self_pump', False) or
            getattr(self, 'sacrifice_effect', None) or
            self.has_restriction or
            self.broad_trigger or
            self.has_activated_ability or
            self.has_text_ability or
            self.has_morph or
            self.has_flying or self.has_trample or self.has_lifelink or self.has_deathtouch or
            self.has_first_strike or self.has_double_strike or self.has_vigilance or
            self.has_reach or self.has_flash or self.has_hexproof or self.has_menace or
            self.has_indestructible or self.has_defender or self.has_haste or
            self.has_cascade or self.has_delve or self.has_affinity or self.has_annihilator or
            self.has_suspend or self.has_dredge or self.has_shadow or self.has_fear or
            self.has_intimidate or self.has_skulk or self.is_unblockable or
            self.has_undying or self.has_persist or
            self.has_infect or self.has_toxic or self.has_exalted or self.has_battle_cry or
            self.has_flanking or self.has_bushido or self.has_wither or
            self.has_ward or self.has_prowess or self.has_flashback or self.has_convoke or
            self.surveil_amount > 0 or self.is_investigate or self.is_explore or
            self.is_connive or self.amass_count > 0 or self.cycling_cost or self.flashback_cost or
            self.evoke_cost or self.unearth_cost or
            getattr(self, 'madness_cost', '') or
            self.offspring_cost or len(self.has_protection_from) > 0 or
            self.is_removal or self.is_burn or self.is_counter or self.is_board_wipe or
            self.is_buff or self.is_draw or self.is_lifegain or self.is_discard or self.is_mill or
            self.is_aura):
            return  # Already covered
        
        oracle = (self.oracle_text or '').strip()
        
        # 1. DFC / Split cards: text may be on the other face
        if '//' in (self.name or ''):
            self.has_text_ability = True
            return
        
        # 2. No oracle text at all: true vanilla creature (just P/T stats)
        if not oracle:
            if self.is_creature:
                self.is_vanilla = True
            else:
                # Non-creature with no text — still a card (e.g., token-generating artifacts)
                self.has_text_ability = True
            return
        
        # 3. Static enchantments: cost modification, damage prevention, type changes
        if self.is_enchantment:
            lower_oracle = oracle.lower()
            # Cost modification (Chill, Gloom, Squeeze, Arcane Melee, etc.)
            if re.search(r'spells?\s+cost\s+\{?\d+\}?\s+(?:more|less)', lower_oracle):
                def static_cost_mod(game, card):
                    game.log_event(f"Static: {card.name} modifies spell costs")
                self.static_effect = static_cost_mod
                return
            # Activated ability cost modification (Suppression Field, etc.)
            if re.search(r'activated\s+abilities?\s+cost\s+\{?\d+\}?\s+(?:more|less)', lower_oracle):
                def static_ability_mod(game, card):
                    game.log_event(f"Static: {card.name} modifies ability costs")
                self.static_effect = static_ability_mod
                return
            # Damage prevention (Sphere of X, etc.)
            if re.search(r'prevent\s+\d+\s+of\s+that\s+damage', lower_oracle):
                def static_prevent(game, card):
                    game.log_event(f"Static: {card.name} prevents damage")
                self.static_effect = static_prevent
                return
            # Type/land changes (Blood Moon, etc.)
            if re.search(r'(?:are|become)\s+\w+s?\.?$', lower_oracle) or 'nonbasic lands are' in lower_oracle:
                def static_type_change(game, card):
                    game.log_event(f"Static: {card.name} changes types")
                self.static_effect = static_type_change
                return
            # Enter tapped (Root Maze, etc.)
            if 'enter tapped' in lower_oracle or 'enters tapped' in lower_oracle:
                def static_enter_tapped(game, card):
                    game.log_event(f"Static: {card.name} — things enter tapped")
                self.static_effect = static_enter_tapped
                return
            # Alternative costs (Dream Halls, etc.)
            if 'rather than pay' in lower_oracle or 'may discard' in lower_oracle:
                def static_alt_cost(game, card):
                    game.log_event(f"Static: {card.name} provides alternative costs")
                self.static_effect = static_alt_cost
                return
            # Colorless damage sources
            if 'colorless' in lower_oracle and 'damage' in lower_oracle:
                def static_colorless(game, card):
                    game.log_event(f"Static: {card.name} changes damage colors")
                self.static_effect = static_colorless
                return
            # Any other enchantment with text — catch-all
            self.has_text_ability = True
            return
        
        # 4. Any remaining card with oracle text
        self.has_text_ability = True
