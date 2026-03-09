"""Tests for the SideboardAgent logic."""

from engine.card import Card
from engine.deck import Deck
from agents.sideboard_agent import SideboardAgent

def test_sideboard_graveyard_hate():
    """Verify that SideboardAgent swaps in Graveyard hate against Graveyard decks."""
    
    # 1. Create Opponent's deck (Graveyard combo)
    opp_deck = Deck()
    gy_card = Card(name="Reanimate", cost="{B}", type_line="Sorcery", oracle_text="Return target creature card from a graveyard to the battlefield under your control. You lose life equal to its mana value.")
    opp_deck.add_card(gy_card, 40) # Lots of graveyard stuff
    
    # 2. Create Our deck
    my_deck = Deck()
    
    # Maindeck has a useless card against combo (like a slow, heavy creature)
    useless_card = Card(name="Colossal Dreadmaw", cost="{4}{G}{G}", type_line="Creature - Dinosaur", oracle_text="Trample", base_power=6, base_toughness=6)
    my_deck.add_card(useless_card, 4)
    
    # Sideboard has Rest in Peace (Graveyard hate)
    hate_card = Card(name="Rest in Peace", cost="{1}{W}", type_line="Enchantment", oracle_text="When Rest in Peace enters the battlefield, exile all graveyards. If a card or token would be put into a graveyard from anywhere, exile it instead.")
    my_deck.add_card(hate_card, 4, sideboard=True)
    
    # 3. Sideboard
    agent = SideboardAgent(my_deck)
    agent.sideboard_against(opp_deck)
    
    # 4. Verify swap
    # The blueprint should now contain Rest in Peace and be missing some Dreadmaws
    maindeck_names = [c.name for c, qty in my_deck._blueprints]
    assert "Rest in Peace" in maindeck_names, "Sideboard hate card was not swapped in!"
    
    remaining_dreadmaws = sum(qty for c, qty in my_deck._blueprints if c.name == "Colossal Dreadmaw")
    assert remaining_dreadmaws < 4, "Useless maindeck cards were not swapped out!"


def test_sideboard_anti_aggro():
    """Verify that SideboardAgent swaps in Board Wipes against Aggro decks."""
    
    # 1. Create Opponent's deck (Aggro - 30 creatures)
    opp_deck = Deck()
    goblin = Card(name="Goblin Guide", cost="{R}", type_line="Creature - Goblin", base_power=2, base_toughness=2)
    opp_deck.add_card(goblin, 30)
    
    # 2. Create Our deck
    my_deck = Deck()
    
    # Maindeck has a slow card
    slow_card = Card(name="Meteor Golem", cost="{7}", type_line="Artifact Creature - Golem", oracle_text="When Meteor Golem enters the battlefield, destroy target nonland permanent.", base_power=3, base_toughness=3)
    my_deck.add_card(slow_card, 4)
    
    # Sideboard has a Board Wipe
    wipe_card = Card(name="Wrath of God", cost="{2}{W}{W}", type_line="Sorcery", oracle_text="Destroy all creatures. They can't be regenerated.")
    my_deck.add_card(wipe_card, 4, sideboard=True)
    
    # 3. Sideboard
    agent = SideboardAgent(my_deck)
    agent.sideboard_against(opp_deck)
    
    # 4. Verify swap
    maindeck_names = [c.name for c, qty in my_deck._blueprints]
    assert "Wrath of God" in maindeck_names, "Board wipe was not swapped in against aggro!"
    
    remaining_slow = sum(qty for c, qty in my_deck._blueprints if c.name == "Meteor Golem")
    assert remaining_slow < 4, "Slow maindeck cards were not swapped out!"
