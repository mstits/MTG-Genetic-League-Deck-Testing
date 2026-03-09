from engine.card import Card
from engine.game import Game
from engine.player import Player
from engine.deck import Deck

def test_offspring_mechanic():
    # Setup
    p1 = Player("P1", Deck())
    p2 = Player("P2", Deck())
    game = Game([p1, p2])
    game.active_player_index = 0
    game.priority_player_index = 0
    game.phase_index = 3
    game.current_phase = "Main 1"
    mouse = Card(
        name="Flowerfoot Swordmaster",
        cost="{1}{W}",
        type_line="Creature - Mouse Soldier",
        oracle_text="Offspring {2}\nLifelink",
        base_power=1,
        base_toughness=2
    )
    p1.hand.add(mouse)
    
    # Provide mana via pool (for `can_pay_cost` checks)
    p1.mana_pool['W'] = 1
    p1.mana_pool['C'] = 3  # Total 4 mana available (can pay 1W + 2)
    
    # Get legal actions — should include announce_cast for this creature
    actions = game.get_legal_actions()
    
    # Find the announce_cast action
    cast_action = next((a for a in actions if a['type'] == 'announce_cast' and 
                        a['card'].name == "Flowerfoot Swordmaster"), None)
    assert cast_action is not None, "Announce cast action should be available for offspring creature"
    
    # Start casting
    game.apply_action(cast_action)
    
    # Should be in pending_cast choices state — choose offspring
    actions = game.get_legal_actions()
    offspring_choice = next((a for a in actions if a['type'] == 'choose_offspring'), None)
    assert offspring_choice is not None, "Choose offspring action should be available"
    
    # Choose offspring
    game.apply_action(offspring_choice)
    
    # Done with choices
    done_choices = next(a for a in game.get_legal_actions() if a['type'] == 'done_choices')
    game.apply_action(done_choices)
    
    # Done with targeting 
    done_target = next(a for a in game.get_legal_actions() if a['type'] == 'done_targeting')
    game.apply_action(done_target)
    
    # Pay costs
    pay = next(a for a in game.get_legal_actions() if a['type'] == 'pay_costs')
    game.apply_action(pay)
    
    # Mouse should be on stack with offspring paid
    assert len(game.stack) >= 1
    mouse_on_stack = game.stack.cards[-1]
    assert getattr(mouse_on_stack, 'was_offspring_paid', False) is True, \
        "Offspring should be paid"


def test_blight_mechanic():
    p1 = Player("P1", Deck())
    p2 = Player("P2", Deck())
    game = Game([p1, p2])
    
    blight_crab = Card(
        name="Blightshore Crab",
        cost="{2}{U}",
        type_line="Creature - Crab",
        oracle_text="Blight",
        base_power=2,
        base_toughness=3
    )
    blight_crab.controller = p1
    
    grizzly_bears = Card(
        name="Grizzly Bears",
        cost="{1}{G}",
        type_line="Creature - Bear",
        base_power=2,
        base_toughness=2
    )
    grizzly_bears.controller = p2
    
    game.battlefield.add(blight_crab)
    game.battlefield.add(grizzly_bears)
    
    # Set up combat
    game.current_phase = "Combat Damage"
    game.active_player_index = 0
    game.combat_attackers = [blight_crab]
    game.combat_blockers = {blight_crab.id: [grizzly_bears]}
    
    # Resolve combat
    game.resolve_combat_damage()
    
    # Grizzly bears should have received 2 -1/-1 counters, killing it (0 toughness)
    assert grizzly_bears.damage_taken == 0
    assert grizzly_bears.counters.get('-1/-1', 0) == 2
    
    # Blight crab takes 2 normal damage
    assert blight_crab.damage_taken == 2
    
    # State-based actions should kill the grizzly bears
    game.check_state_based_actions()
    
    
    assert grizzly_bears not in game.battlefield.cards
    assert grizzly_bears in p2.graveyard.cards
    assert blight_crab in game.battlefield.cards

if __name__ == '__main__':
    test_offspring_mechanic()
    test_blight_mechanic()
    print("All tests passed!")

