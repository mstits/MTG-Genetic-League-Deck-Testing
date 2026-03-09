"""Final Smoke Test for MTG Genetic League.

Runs an automated Play-Loop of a Bracket 5 cEDH game to completion
without errors or glitches, verifying the engine is turnkey ready.
"""

from engine.game import Game
from engine.player import Player
from engine.deck import Deck
from engine.card import Card
import logging

logging.basicConfig(level=logging.INFO)

def build_cedh_deck():
    d = Deck()
    # 100 cards
    for _ in range(40):
        d.add_card(Card("Island", "", "Basic Land - Island", "{T}: Add {U}.", produced_mana=["U"]), 1)
        
    for _ in range(10):
        d.add_card(Card("Mana Crypt", "{0}", "Artifact", "At the beginning of your upkeep, flip a coin. If you lose, Mana Crypt deals 3 damage to you.\n{T}: Add {C}{C}."), 1)
        d.add_card(Card("Thassa's Oracle", "{U}{U}", "Creature - Merfolk Wizard", "When Thassa's Oracle enters the battlefield, look at the top X cards of your library, where X is your devotion to blue. If X is greater than or equal to the number of cards in your library, you win the game.", 1, 3), 1)
        d.add_card(Card("Demonic Consultation", "{B}", "Instant", "Name a card. Exile the top six cards of your library, then reveal cards from the top of your library until you reveal the named card. Put that card into your hand and exile all other cards revealed this way."), 1)
        d.add_card(Card("Force of Will", "{3}{U}{U}", "Instant", "You may pay 1 life and exile a blue card from your hand rather than pay this spell's mana cost.\nCounter target spell."), 1)
        d.add_card(Card("Ad Nauseam", "{3}{B}{B}", "Instant", "Reveal the top card of your library and put that card into your hand. You lose life equal to its mana value. You may repeat this process any number of times."), 1)
        d.add_card(Card("Rhystic Study", "{2}{U}", "Enchantment", "Whenever an opponent casts a spell, they may pay {1}. If they don't, you may draw a card."), 1)
    return d

def run_smoke_test():
    print("Initializing Bracket 5 cEDH Smoke Test...")
    p1 = Player("Spike", build_cedh_deck())
    p2 = Player("Johnny", build_cedh_deck())
    
    # Actually deck names don't do much in engine tests, but let's set them
    p1.original_deck.name = "Grixis Consult"
    p2.original_deck.name = "Blue Farm"
    
    # Force 40 life for Commander/cEDH
    p1.life = 40
    p2.life = 40
    
    from simulation.runner import SimulationRunner
    from agents.strategic_agent import StrategicAgent
    
    game = Game([p1, p2])
    agent1 = StrategicAgent(p1)
    agent2 = StrategicAgent(p2)
    runner = SimulationRunner(game, [agent1, agent2])
    print("Starting automated play-loop via SimulationRunner...")
    
    try:
        result = runner.run()
        print(f"Match completed successfully! Winner: {result.winner}")
        print(f"Total Turns: {result.turns}")
        return True
    except Exception as e:
        print(f"Smoke Test Failed with exception: {e}")
        import traceback
        traceback.print_exc()
        return False

if __name__ == "__main__":
    success = run_smoke_test()
    if not success:
        exit(1)
