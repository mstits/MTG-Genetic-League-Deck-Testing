import math

def nCr(n, r):
    if r > n or r < 0:
        return 0
    return math.comb(n, r)

def hypergeom_pmf(N, K, n, k):
    """
    Probability of exactly k successes in n draws from a population of N containing K successes.
    """
    return (nCr(K, k) * nCr(N - K, n - k)) / nCr(N, n)

def hypergeom_cdf_at_least(N, K, n, k):
    """
    Probability of AT LEAST k successes in n draws.
    """
    if k <= 0: return 1.0
    if K < k: return 0.0
    prob = 0.0
    for i in range(k, min(K, n) + 1):
        prob += hypergeom_pmf(N, K, n, i)
    return prob

def calculate_mana_requirements(deck_size, cast_cmc, color_devotion, sources):
    """
    Calculate the probability of drawing precisely the required amount of a specific colored source 
    by the turn we want to cast the spell.
    
    deck_size: typically 60
    cast_cmc: the turn the spell is typically cast (e.g. 1 for {R}, 3 for {1}{W}{W}, 4 for {2}{G}{G})
    color_devotion: the amount of specific colored pips required (e.g. 2 for {1}{W}{W})
    sources: the total number of lands/sources producing that color in the deck
    
    Returns the percentage probability (0.0 to 100.0) of hitting it.
    """
    # Assuming play mode (no draw on turn 1) but we average it out. 
    # Cards seen on Turn X typically = 7 (initial hand) + X (draws per turn if on draw) or X-1 (if on play).
    # Karsten's math assumes an average of 7.5 + X - 1 cards seen. Let's use 6.5 + X.
    
    cards_seen = math.floor(6.5 + cast_cmc)
    
    prob = hypergeom_cdf_at_least(deck_size, sources, cards_seen, color_devotion)
    return prob * 100.0

def evaluate_deck_mana(deck_dict, card_data_pool):
    """
    Evaluates a deck's mana base based on the spell requirements and lands.
    deck_dict: {card_name: count}
    card_data_pool: dict of all cards with parsed attributes
    """
    # 1. Count colored sources
    sources = {'W': 0, 'U': 0, 'B': 0, 'R': 0, 'G': 0, 'C': 0}
    
    # Very rudimentary source counting
    for name, count in deck_dict.items():
        card = card_data_pool.get(name, {})
        types = card.get('type_line', '')
        if 'Land' in types:
            # Check produced mana (heuristic based on colors or text)
            produced = card.get('produced_mana', card.get('color_identity', []))
            if not produced and 'Wastes' in name:
                produced = ['C']
            for c in produced:
                if c in sources:
                    sources[c] += count
                    
    deck_size = sum(deck_dict.values())
    if deck_size < 40: deck_size = 60 # Default to 60 for math purposes if incomplete
    
    results = {}
    
    # 2. Evaluate hardest spells to cast for each color
    for name, count in deck_dict.items():
        card = card_data_pool.get(name, {})
        if 'Land' in card.get('type_line', ''): continue
        if not card.get('mana_cost'): continue
        
        cost_str = card.get('mana_cost')
        cmc = card.get('cmc', 0)
        if cmc == 0: continue
        
        for color in ['W', 'U', 'B', 'R', 'G']:
            pips = cost_str.count(f'{{{color}}}')
            if pips > 0:
                prob = calculate_mana_requirements(deck_size, max(1, cmc), pips, sources[color])
                
                # We only flag if prob < 90%
                # Or keep track of the most "greedy" requirement per color
                if color not in results or pips > results[color]['pips'] or (pips == results[color]['pips'] and cmc < results[color]['cmc']):
                    results[color] = {
                        "card": name,
                        "pips": pips,
                        "cmc": cmc,
                        "sources": sources[color],
                        "probability": round(prob, 1)
                    }
                    
    # Format output
    issues = []
    for color, req in results.items():
        status = "OK"
        if req['probability'] < 80: status = "DANGER"
        elif req['probability'] < 90: status = "WARNING"
        
        req['status'] = status
        if status != "OK":
            issues.append(req)
            
    return {"sources": sources, "requirements": results, "issues": issues}
