import cProfile
import pstats
import io
from engine.game import Game
from engine.player import Player
from engine.deck import Deck
from engine.card import Card
from agents.strategic_agent import StrategicAgent
from simulation.runner import SimulationRunner

def build_cedh_deck():
    d = Deck()
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

def run_simulation():
    p1 = Player("P1", build_cedh_deck())
    p2 = Player("P2", build_cedh_deck())
    p1.life = 20
    p2.life = 20
    game = Game([p1, p2])
    # StrategicAgent without lookahead to profile base engine + scoring heuristics
    agent1 = StrategicAgent(look_ahead_depth=0)
    agent2 = StrategicAgent(look_ahead_depth=0)
    runner = SimulationRunner(game, [agent1, agent2], capture_snapshots=False)
    runner.run()

if __name__ == "__main__":
    pr = cProfile.Profile()
    pr.enable()
    run_simulation()
    pr.disable()
    
    s = io.StringIO()
    sortby = 'cumulative'
    ps = pstats.Stats(pr, stream=s).sort_stats(sortby)
    ps.print_stats(30)
    print(s.getvalue())
