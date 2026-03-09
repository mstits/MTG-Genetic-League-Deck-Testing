"""Lorwyn Eclipsed Mechanics (Feb 2026 Set).

Handles parsing and application of block-specific mechanics like Blight and Vivid.
"""

def process_lorwyn_eclipsed_mechanics(card):
    """Parse Lorwyn Eclipsed keywords from oracle text."""
    lower_text = card.oracle_text.lower()
    
    # Blight: Handled natively in game.py combat damage step
    # Vivid: Enters the battlefield tapped with 2 charge counters
    if 'vivid' in lower_text:
        card.has_vivid = True
        
        existing_etb = card.etb_effect
        def vivid_etb(game, c):
            c.counters['charge'] = c.counters.get('charge', 0) + 2
            c.tapped = True
            game.log_event(f"T{game.turn_count}: {c.name} enters tapped with 2 charge counters (Vivid).")
            if existing_etb:
                existing_etb(game, c)
        
        card.etb_effect = vivid_etb
