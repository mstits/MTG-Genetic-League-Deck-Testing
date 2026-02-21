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
import itertools

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
    
    # --- NEW: Additional keyword/mechanic fields ---
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
    combat_damage_trigger: Optional[Callable[['Game', 'Card'], None]] = None  # "whenever ~ deals combat damage to a player"
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
        # (e.g. 'reach' in 'breaching', 'haste' in 'chaste')
        keywords = {
            'haste': 'has_haste', 'flying': 'has_flying', 'trample': 'has_trample',
            'lifelink': 'has_lifelink', 'deathtouch': 'has_deathtouch',
            'first strike': 'has_first_strike', 'double strike': 'has_double_strike',
            'vigilance': 'has_vigilance', 'reach': 'has_reach',
            'flash': 'has_flash', 'hexproof': 'has_hexproof',
            'menace': 'has_menace', 'indestructible': 'has_indestructible',
            'defender': 'has_defender',
        }
        for kw, attr in keywords.items():
            if re.search(r'\b' + re.escape(kw) + r'\b', lower_text):
                setattr(self, attr, True)
        
        # Parse creature types from type_line
        if 'Creature' in self.type_line:
            parts = self.type_line.split('—')
            if len(parts) > 1:
                self.creature_types = [t.strip() for t in parts[1].split()]
        
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
        self._parse_kicker(lower_text)
        self._parse_cycling(lower_text)
        self._parse_vehicle(lower_text)
        self._parse_prowess(lower_text)
        
        # Parse effects (spells + ETB)
        self.parse_effects()
        self.parse_etb_effects()
        
        # ETB sacrifice MUST run AFTER parse_etb_effects so it can chain
        # with (not overwrite) any ETB effect that was parsed
        self._parse_etb_sacrifice(lower_text)
    
    # ─── NEW PARSERS ──────────────────────────────────────────────
    
    def _parse_enters_tapped(self, text: str):
        """Parse 'enters the battlefield tapped' (Rule 305.7)."""
        if 'enters the battlefield tapped' in text or 'enters tapped' in text:
            self.enters_tapped = True

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
        
        # "draw a card"
        if 'draw a card' in text:
            def death_draw(game, card):
                card.controller.draw_card(1)
                game.log_event(f"Death: {card.name} — {card.controller.name} draws a card")
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
    
    def _parse_static_effect(self, text: str):
        """Parse static/continuous effects like anthems (Rule 613)."""
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
        
        # Tribal: "other [Type] you control get +N/+N"
        tribal = re.search(r'other (\w+)s? you control get \+(\d+)/\+(\d+)', text)
        if tribal:
            creature_type = tribal.group(1).capitalize()
            p_buff = int(tribal.group(2))
            t_buff = int(tribal.group(3))
            self.static_effect = {'power': p_buff, 'toughness': t_buff, 'filter': 'type', 'type': creature_type}
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
            
        # Parse Equip cost: "Equip {N}"
        equip_match = re.search(r'equip \{(\d+)\}', text)
        if equip_match:
            self.equip_cost = f"{{{equip_match.group(1)}}}"
        
        # Parse stat bonuses: "Equipped creature gets +N/+N"
        bonus_match = re.search(r'equipped creature gets \+(\d+)/\+(\d+)', text)
        if bonus_match:
            self.equip_bonus = {'power': int(bonus_match.group(1)), 'toughness': int(bonus_match.group(2))}

    def _parse_aura(self, text: str):
        """Parse Aura keywords and abilities."""
        if 'Aura' not in self.type_line:
            return
        
        self.is_aura = True
        
        # Parse enchant target: "Enchant creature"
        enchant_match = re.search(r'enchant (\w+)', text)
        if enchant_match:
            self.enchant_target_type = enchant_match.group(1)
            
        # Parse stat bonuses: "Enchanted creature gets +N/+N"
        bonus_match = re.search(r'enchanted creature gets \+(\d+)/\+(\d+)', text)
        if bonus_match:
            self.equip_bonus = {'power': int(bonus_match.group(1)), 'toughness': int(bonus_match.group(2))}
    
    # ─── PROPERTIES ───────────────────────────────────────────────
    
    @property
    def power(self) -> Optional[int]:
        """Effective power = base + temporary modifiers + counters + attachments."""
        if self.base_power is None:
            return None
        counter_bonus = self.counters.get('+1/+1', 0) - self.counters.get('-1/-1', 0)
        
        # Add bonuses from attachments (Equipment/Auras)
        attachment_bonus = 0
        for att in self.attachments:
            attachment_bonus += att.equip_bonus.get('power', 0)
            
        return self.base_power + sum(m.get('power', 0) for m in self._temp_modifiers) + counter_bonus + attachment_bonus
    
    @power.setter
    def power(self, value):
        """Setting power directly sets the base (for permanent effects like +1/+1 counters)."""
        self.base_power = value
    
    @property
    def toughness(self) -> Optional[int]:
        """Effective toughness = base + temporary modifiers + counters + attachments."""
        if self.base_toughness is None:
            return None
        counter_bonus = self.counters.get('+1/+1', 0) - self.counters.get('-1/-1', 0)
        
        # Add bonuses from attachments
        attachment_bonus = 0
        for att in self.attachments:
            attachment_bonus += att.equip_bonus.get('toughness', 0)
            
        return self.base_toughness + sum(m.get('toughness', 0) for m in self._temp_modifiers) + counter_bonus + attachment_bonus
    
    @toughness.setter
    def toughness(self, value):
        """Setting toughness directly sets the base (for permanent effects)."""
        self.base_toughness = value
    
    def clear_temp_modifiers(self):
        """Clear all 'until end of turn' effects (Rule 514.2).
        Preserves static-tagged modifiers (anthems) since those are recalculated."""
        self._temp_modifiers = [m for m in self._temp_modifiers if m.get('_static')]

    def __repr__(self):
        return f"{self.name}"

    def __deepcopy__(self, memo):
        """Ensure each deepcopied Card gets a fresh unique ID."""
        import copy
        cls = self.__class__
        result = cls.__new__(cls)
        memo[id(self)] = result
        for k, v in self.__dict__.items():
            if k == 'id':
                # Assign a fresh unique ID instead of copying the old one
                setattr(result, k, next(_card_id_counter))
            else:
                setattr(result, k, copy.deepcopy(v, memo))
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
        
        # ETB damage
        etb_dmg = re.search(r'(?:when|whenever) .* enters (?:the battlefield|play).* deals? (\d+) damage', text)
        if not etb_dmg:
            etb_dmg = re.search(r'enters (?:the battlefield|play).* deals? (\d+) damage', text)
        if etb_dmg:
            amount = int(etb_dmg.group(1))
            effects.append(self._make_etb_damage(amount))
            self.is_burn = True
        
        # ETB draw
        etb_draw = re.search(r'(?:when|whenever) .* enters (?:the battlefield|play).* draw (\d+)', text)
        if not etb_draw:
            etb_draw = re.search(r'enters (?:the battlefield|play).* draw (\d+)', text)
        if etb_draw:
            amount = int(etb_draw.group(1))
            effects.append(self._make_etb_draw(amount))
            self.is_draw = True
        
        # ETB destroy
        if re.search(r'(?:when|whenever) .* enters (?:the battlefield|play).* destroy target creature', text):
            effects.append(self._make_etb_destroy())
            self.is_removal = True
        
        # ETB gain life
        etb_life = re.search(r'(?:when|whenever) .* enters (?:the battlefield|play).* gain (\d+) life', text)
        if etb_life:
            amount = int(etb_life.group(1))
            effects.append(self._make_etb_lifegain(amount))
            self.is_lifegain = True
        
        # ETB bounce
        if re.search(r'(?:when|whenever) .* enters (?:the battlefield|play).* return target.* creature', text):
            effects.append(self._make_etb_bounce())
            self.is_removal = True
        
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

    def parse_effects(self):
        """Parse spell effects from oracle text."""
        text = self.oracle_text.lower()
        
        # Damage (Burn)
        dmg_match = re.search(r"deals (\d+) damage to (any target|target creature|target player|each opponent)", text)
        if dmg_match:
            amount = int(dmg_match.group(1))
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
            # Combine sacrifice-a-creature with whatever main effect follows
            # Find the main effect text after the cost clause
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

    def _make_damage_effect(self, amount: int, target_type: str):
        def effect(game: 'Game', card: 'Card'):
            opp = game.players[(game.players.index(card.controller) + 1) % 2]
            
            # "each opponent" — direct face damage
            if "each opponent" in target_type:
                opp.life -= amount
                game.log_event(f"{card.name} deals {amount} to {opp.name} ({opp.life} life)")
                return
            
            # Check lethal to face
            if ("any target" in target_type or "player" in target_type) and opp.life <= amount:
                opp.life -= amount
                game.log_event(f"{card.name} deals {amount} to {opp.name} (LETHAL)")
                return
            
            # Try to damage a creature (mark damage, let SBAs handle death — Rule 120.6)
            if "any target" in target_type or "creature" in target_type:
                targets = [c for c in game.battlefield.cards 
                          if c.controller == opp and c.is_creature 
                          and not c.has_hexproof and not c.is_protected_from(card)]
                # Target the best creature we can remove via marked damage
                killable = [t for t in targets if (t.toughness or 0) - t.damage_taken <= amount]
                if killable:
                    killable.sort(key=lambda c: c.power or 0, reverse=True)
                    t = killable[0]
                    t.damage_taken += amount
                    game.log_event(f"{card.name} deals {amount} damage to {t.name} (dmg={t.damage_taken}/{t.toughness})")
                    return
                # If no killable target, still damage the biggest threat
                if targets:
                    targets.sort(key=lambda c: c.power or 0, reverse=True)
                    t = targets[0]
                    t.damage_taken += amount
                    game.log_event(f"{card.name} deals {amount} damage to {t.name} (dmg={t.damage_taken}/{t.toughness})")
                    return
            
            # Fallback — face
            if "any target" in target_type or "player" in target_type:
                opp.life -= amount
                game.log_event(f"{card.name} deals {amount} to {opp.name} ({opp.life} life)")
        return effect

    def _make_destroy_effect(self, target_type: str):
        def effect(game: 'Game', card: 'Card'):
            opp = game.players[(game.players.index(card.controller) + 1) % 2]
            targets = [c for c in game.battlefield.cards 
                      if c.controller == opp and c.is_creature 
                      and not c.has_hexproof and not c.is_protected_from(card)]
            targets.sort(key=lambda c: c.power or 0, reverse=True)
            if targets:
                t = targets[0]
                if not t.has_indestructible:
                    game.battlefield.remove(t); t.controller.graveyard.add(t)
                    game.log_event(f"{card.name} destroys {t.name}")
        return effect
    
    def _make_destroy_permanent_effect(self):
        def effect(game: 'Game', card: 'Card'):
            opp = game.players[(game.players.index(card.controller) + 1) % 2]
            targets = [c for c in game.battlefield.cards 
                      if c.controller == opp and (c.is_enchantment or c.is_artifact) and not c.has_hexproof]
            if targets:
                t = targets[0]
                game.battlefield.remove(t); t.controller.graveyard.add(t)
                game.log_event(f"{card.name} destroys {t.name}")
        return effect
    
    def _make_exile_effect(self):
        def effect(game: 'Game', card: 'Card'):
            opp = game.players[(game.players.index(card.controller) + 1) % 2]
            targets = [c for c in game.battlefield.cards 
                      if c.controller == opp and c.is_creature 
                      and not c.has_hexproof and not c.is_protected_from(card)]
            targets.sort(key=lambda c: c.power or 0, reverse=True)
            if targets:
                t = targets[0]
                game.battlefield.remove(t); t.controller.exile.add(t)
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
            # Counter the most recent spell on the stack (Rule 701.6a)
            if len(game.stack) > 0:
                target = game.stack.cards.pop()
                # Only counter actual spells, not triggers
                if isinstance(target, Card):
                    (target.controller or game.active_player).graveyard.add(target)
                    game.log_event(f"{card.name} counters {target.name}!")
                else:
                    # Can't counter abilities, put it back
                    game.stack.cards.append(target)
                    game.log_event(f"{card.name} fizzles (target is an ability, not a spell)")
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
            # Buff the strongest friendly creature (until end of turn — Rule 514.2)
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
        name_ref = self.name.lower().split(',')[0].strip()  # First word of name
        pattern = rf'whenever {re.escape(name_ref)} attacks'
        alt_pattern = r'whenever .* attacks'
        
        if not re.search(pattern, text) and not re.search(r'whenever ~ attacks', text.replace(name_ref, '~')):
            # Try generic "this creature attacks" pattern
            if 'whenever this creature attacks' not in text and not re.search(pattern, text):
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
            count = 0
            for perm in game.battlefield.cards:
                if perm.controller == card.controller and perm.counters:
                    # Add one of each type of counter already on it
                    for ctype in list(perm.counters.keys()):
                        perm.counters[ctype] += 1
                    count += 1
                    game.log_event(f"Proliferate: {perm.name} gets extra counters")
            if count == 0:
                game.log_event(f"{card.name}: Proliferate (no targets with counters)")
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
            # Sacrifice weakest own creature
            own = [c for c in game.battlefield.cards
                  if c.controller == card.controller and c.is_creature]
            if own:
                sac = min(own, key=lambda c: c.power or 0)
                game.battlefield.remove(sac)
                card.controller.graveyard.add(sac)
                game.log_event(f"{card.name}: sacrifices {sac.name}")
            # Deal damage
            opp.life -= amount
            game.log_event(f"{card.name} deals {amount} to {opp.name} ({opp.life} life)")
        return effect

    def _make_sac_creature_draw_effect(self, count: int):
        """Sacrifice a creature, then draw N cards."""
        def effect(game: 'Game', card: 'Card'):
            own = [c for c in game.battlefield.cards
                  if c.controller == card.controller and c.is_creature]
            if own:
                sac = min(own, key=lambda c: c.power or 0)
                game.battlefield.remove(sac)
                card.controller.graveyard.add(sac)
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

    def _parse_prowess(self, text: str):
        """Parse prowess keyword (Rule 702.107)."""
        if 'prowess' in text.split():
            self.has_prowess = True
