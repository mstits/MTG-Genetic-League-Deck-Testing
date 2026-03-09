"""layers — Continuous Effects Layer System (Rule 613).

Magic's Layer system dictates how continuous effects apply to objects.
This engine strictly follows the 7 main layers for P/T and characteristics:
Layer 1: Copy effects
Layer 2: Control-changing effects
Layer 3: Text-changing effects
Layer 4: Type-changing effects
Layer 5: Color-changing effects
Layer 6: Ability-adding/removing effects
Layer 7: Power/Toughness changing effects
    7a: CDAs (Characteristic-Defining Abilities)
    7b: Set P/T to a specific value
    7c: Modify P/T (e.g., +X/+Y)
    7d: P/T switches
"""

from typing import List, Any
import copy

class LayerEngine:
    """Processes all active continuous effects on the battlefield in order.
    
    IMPORTANT: This engine works with _true_base_power/_true_base_toughness 
    (the original card stats) and writes final computed values to base_power/
    base_toughness. The Card.power/toughness property getter then adds 
    counters and temp modifiers on top.
    """

    def __init__(self, game):
        self.game = game

    def apply_layers(self, effect_sources: List[Any], tracked_auras: List[Any]):
        """Runs the Rule 613 Layer system across all battlefield permanents.
        
        This saves the original base stats, computes all layer effects on
        local variables, then writes the final result to base_power/base_toughness.
        The Card.power property getter adds counters and temp mods on top.
        """
        # 0. Prep: Initialize _true_base if not set, then reset base to true base
        for target in self.game.battlefield.cards:
            if target.is_creature:
                # Save the original printed P/T (only once, on first encounter)
                if not hasattr(target, '_true_base_power') or target._true_base_power is None:
                    target._true_base_power = target.base_power
                    target._true_base_toughness = target.base_toughness
                
                # Reset to true base before recalculating layers
                target.base_power = target._true_base_power
                target.base_toughness = target._true_base_toughness
                
                # Clear dynamic granted abilities
                target._granted_keywords = set()
                target._lost_keywords = set()
            
            # Save original text/types for Blood Moon
            if target.is_land:
                if getattr(target, '_true_type_line', None) is None:
                    target._true_type_line = target.type_line
                    target._true_produced_mana = list(getattr(target, 'produced_mana', []))
                    target._true_oracle_text = getattr(target, 'oracle_text', '')
                
                # Reset to true string/lists before calculating layers
                target.type_line = target._true_type_line
                target.produced_mana = list(target._true_produced_mana)
                target.oracle_text = target._true_oracle_text

        # Gather all valid sources of static effects
        valid_sources = []
        for source in effect_sources:
            se = getattr(source, 'static_effect', None)
            if se and isinstance(se, dict):
                valid_sources.append(source)

        self._valid_sources = valid_sources

        # Layers 1-5 (Type changing effects, etc)
        self._apply_layer_1_to_5()

        # Pre-Layer: Reset game-level static effect tracking
        self.game._active_cost_modifiers = []
        self.game._active_restrictions = []

        # Layer 6: Ability-adding/removing effects
        for source in valid_sources:
            se = source.static_effect
            se_type = se.get('type', '')
            
            if 'grant_ability' in se or 'remove_ability' in se:
                self._apply_layer_6(source, se)
            
            # keyword_grant: "creatures you control have flying"
            elif se_type == 'keyword_grant':
                keyword = se.get('keyword', '')
                if keyword:
                    kw_attr = f'has_{keyword}' if not keyword.startswith('has_') else keyword
                    for target in self.game.battlefield.cards:
                        if target.is_creature and target.controller == source.controller:
                            target._granted_keywords.add(kw_attr)
            
            # cost_modifier: "spells cost {1} more/less to cast"
            elif se_type == 'cost_modifier':
                self.game._active_cost_modifiers.append({
                    'source': source,
                    'amount': se.get('amount', 1),
                    'direction': se.get('direction', 'more'),
                    'filter': se.get('filter', 'all')
                })
            
            # restriction: "can't attack/block" style effects
            elif se_type == 'restriction':
                self.game._active_restrictions.append({
                    'source': source,
                    'text': se.get('text', '')
                })
            
            # global_effect: "each creature/player" effects
            elif se_type == 'global_effect':
                self.game._active_restrictions.append({
                    'source': source,
                    'text': se.get('text', '')
                })
                
        # Equipments/Auras adding abilities (Layer 6)
        for aura in tracked_auras:
            self._apply_aura_layer_6(aura)

        # Layer 7: Power/Toughness changing effects
        # 7a: CDAs (Characteristic-Defining Abilities, CR 604.3)
        for target in self.game.battlefield.cards:
            if not target.is_creature:
                continue
            cda = getattr(target, 'cda_type', '')
            if not cda:
                continue
            
            if cda == 'deaths_shadow':
                # "Gets -X/-X, where X is your life total"
                life = target.controller.life if target.controller else 20
                target.base_power = (target.base_power or 0) - life
                target.base_toughness = (target.base_toughness or 0) - life
                
            elif cda == 'tarmogoyf':
                # P = card types in all GYs, T = that + 1
                card_types = set()
                for p in self.game.players:
                    for c in p.graveyard.cards:
                        tl = getattr(c, 'type_line', '')
                        for t in ['Creature', 'Instant', 'Sorcery', 'Enchantment',
                                   'Artifact', 'Planeswalker', 'Land', 'Tribal']:
                            if t in tl:
                                card_types.add(t)
                count = len(card_types)
                target.base_power = count
                target.base_toughness = count + 1
                
            elif cda == 'scourge_skyclaves':
                # "P/T = 20 minus the highest life total among players"
                highest = max(p.life for p in self.game.players)
                val = 20 - highest
                target.base_power = val
                target.base_toughness = val
                
            elif cda == 'serra_avatar':
                # "P/T = your life total"
                life = target.controller.life if target.controller else 0
                target.base_power = life
                target.base_toughness = life
                
            elif cda == 'nighthowler':
                # "P/T = creature cards in all graveyards"
                count = sum(
                    1 for p in self.game.players
                    for c in p.graveyard.cards if c.is_creature
                )
                target.base_power = (target._true_base_power or 0) + count
                target.base_toughness = (target._true_base_toughness or 0) + count

        # 7b: Effects that set P/T to a specific number/value
        for source in valid_sources:
            se = source.static_effect
            if 'set_power' in se or 'set_toughness' in se:
                self._apply_layer_7b(source, se)

        # 7c: Effects that modify P/T (e.g., +3/+3, Auras, Equipment)
        for source in valid_sources:
            se = source.static_effect
            if 'power' in se or 'toughness' in se and 'set_power' not in se:
                self._apply_layer_7c(source, se)
                
        for aura in tracked_auras:
            self._apply_aura_layer_7c(aura)

        # 7d: P/T switching (e.g., Inside Out)
        for source in valid_sources:
            se = source.static_effect
            if 'switch_pt' in se and se['switch_pt']:
                self._apply_layer_7d(source, se)

        # Cleanup: Inject granted keywords natively into the tracked state
        for target in self.game.battlefield.cards:
            if target.is_creature:
                if hasattr(target, '_granted_keywords'):
                    for kw in target._granted_keywords:
                        setattr(target, kw, True)
                if hasattr(target, '_lost_keywords'):
                    for kw in target._lost_keywords:
                        setattr(target, kw, False)


    def _static_effect_applies(self, source, target, se: dict) -> bool:
        """Helper to determine if a static effect applies to a target based on the filter."""
        effect_filter = se.get('filter', '')
        if effect_filter == 'other_creatures':
            return target.controller == source.controller and target is not source
        elif effect_filter == 'all_creatures':
            return target.controller == source.controller
        elif effect_filter == 'type':
            req_type = se.get('type', '')
            return target.controller == source.controller and target is not source and getattr(target, 'creature_types', []) and req_type in target.creature_types
        return False

    def _apply_layer_1_to_5(self):
        # Hooks for Copy, Control, Text, Type, Color
        for source in self._valid_sources:
            se = source.static_effect
            if se.get('type') == 'set_land_type':
                self._apply_layer_4_land_type(source, se)

    def _apply_layer_4_land_type(self, source, se):
        land_type = se.get('land_type', 'Mountain')
        effect_filter = se.get('filter', 'nonbasic_lands')
        
        for target in self.game.battlefield.cards:
            if not target.is_land: continue
            
            if effect_filter == 'nonbasic_lands' and 'Basic' not in target.type_line:
                # Rule 305.7: Setting basic land type removes existing abilities generated from its rules text
                target.type_line = f"Land — {land_type}"
                
                # Set mana production based on type
                if land_type == 'Mountain':
                    target.produced_mana = ['R']
                    target.oracle_text = "{T}: Add {R}."
                elif land_type == 'Island':
                    target.produced_mana = ['U']
                    target.oracle_text = "{T}: Add {U}."
                elif land_type == 'Swamp':
                    target.produced_mana = ['B']
                    target.oracle_text = "{T}: Add {B}."
                elif land_type == 'Plains':
                    target.produced_mana = ['W']
                    target.oracle_text = "{T}: Add {W}."
                elif land_type == 'Forest':
                    target.produced_mana = ['G']
                    target.oracle_text = "{T}: Add {G}."

    def _apply_layer_6(self, source, se):
        grant_list = se.get('grant_ability', [])
        remove_list = se.get('remove_ability', [])
        if isinstance(grant_list, str): grant_list = [grant_list]
        if isinstance(remove_list, str): remove_list = [remove_list]
        
        for target in self.game.battlefield.cards:
            if not target.is_creature: continue
            if self._static_effect_applies(source, target, se):
                for ability in grant_list:
                    target._granted_keywords.add(ability)
                for ability in remove_list:
                    target._lost_keywords.add(ability)

    def _apply_aura_layer_6(self, aura):
        target = getattr(aura, 'enchant_target_ptr', None) or getattr(aura, 'equipped_to', None)
        if target and target in self.game.battlefield.cards and target.is_creature:
            # Aura keyword grants (via 'grants' attribute)
            grant_list = getattr(aura, 'grants', [])
            if isinstance(grant_list, str): grant_list = [grant_list]
            for ability in grant_list:
                target._granted_keywords.add(ability)
            
            # Equipment keyword grants (via equip_bonus dict)
            equip_bonus = getattr(aura, 'equip_bonus', {})
            if equip_bonus:
                keyword_map = {
                    'flying': 'has_flying',
                    'trample': 'has_trample',
                    'deathtouch': 'has_deathtouch',
                    'lifelink': 'has_lifelink',
                    'vigilance': 'has_vigilance',
                    'haste': 'has_haste',
                    'first_strike': 'has_first_strike',
                    'double_strike': 'has_double_strike',
                    'menace': 'has_menace',
                    'hexproof': 'has_hexproof',
                    'indestructible': 'has_indestructible',
                }
                for kw_key, kw_attr in keyword_map.items():
                    if equip_bonus.get(kw_key):
                        target._granted_keywords.add(kw_attr)

    def _apply_layer_7b(self, source, se):
        set_p = se.get('set_power', None)
        set_t = se.get('set_toughness', None)
        for target in self.game.battlefield.cards:
            if not target.is_creature: continue
            if self._static_effect_applies(source, target, se):
                if set_p is not None: target.base_power = set_p
                if set_t is not None: target.base_toughness = set_t

    def _apply_layer_7c(self, source, se):
        """Layer 7c: Modify P/T with +X/+Y effects (anthems, lords).
        Writes directly to base_power/base_toughness to avoid property setter corruption."""
        p_buff = se.get('power', 0)
        t_buff = se.get('toughness', 0)
        for target in self.game.battlefield.cards:
            if not target.is_creature: continue
            if self._static_effect_applies(source, target, se):
                target.base_power = (target.base_power or 0) + p_buff
                target.base_toughness = (target.base_toughness or 0) + t_buff

    def _apply_aura_layer_7c(self, aura):
        target = getattr(aura, 'enchant_target_ptr', None) or getattr(aura, 'equipped_to', None)
        if target and target in self.game.battlefield.cards and target.is_creature:
            target.base_power = (target.base_power or 0) + getattr(aura, 'pump_power', 0)
            target.base_toughness = (target.base_toughness or 0) + getattr(aura, 'pump_toughness', 0)

    def _apply_layer_7d(self, source, se):
        for target in self.game.battlefield.cards:
            if not target.is_creature: continue
            if self._static_effect_applies(source, target, se):
                temp = target.base_power
                target.base_power = target.base_toughness
                target.base_toughness = temp
