"""Match log parser — Converts raw text game logs into structured JSON for replay.

Parses turn-by-turn log output from SimulationRunner into a structured
format with player stats (HP, cards, lands, board) and action lists
for each turn, suitable for the web replay viewer.
"""

import re

def parse_match_log(log_content):
    """
    Parses the raw text log into a structured JSON object for the replay viewer.
    Returns: {
        "games": [
            {
                "game_num": 1,
                "winner": "...",
                "turns": [
                    {
                        "turn_num": 1,
                        "p1": {"hp": 20, "cards": 7, "lands": 0, "board": []},
                        "p2": {"hp": 20, "cards": 7, "lands": 0, "board": []},
                        "actions": ["Player A plays Land", ...]
                    },
                    ...
                ]
            }
        ],
        "match_info": {...}
    }
    """
    lines = log_content.split('\n')
    games = []
    current_game = None
    current_turn = None
    
    # Regex patterns
    # --- Game 1 (26 turns, winner: D2930) ---
    game_header_re = re.compile(r"--- Game (\d+) \((\d+) turns, winner: (.*)\) ---")
    
    # --- T1 | D5614 (20hp, 7cards, 0lands) vs D2930 (20hp, 7cards, 0lands) [WP: 0.55] ---
    turn_header_re = re.compile(r"--- T(\d+) \| (.*?) \((\d+)hp, (\d+)cards, (\d+)lands\) vs (.*?) \((\d+)hp, (\d+)cards, (\d+)lands\)(?: \[WP: ([\d.]+)\])? ---")
    
    # Board: D5614 [Token (0/3) 0/3] | D2930 [empty]
    # This might match [empty] or [Card A, Card B]
    board_re = re.compile(r"^\s+Board: (.*?) \[(.*?)\] \| (.*?) \[(.*?)\]")
    
    match_info = {}
    
    for line in lines:
        line = line.strip()
        if not line: continue
        
        # Match Header
        if line.startswith("MATCH:"):
            match_info['title'] = line.replace("MATCH:", "").strip()
            continue
            
        # Game Header
        m_game = game_header_re.match(line)
        if m_game:
            if current_game:
                if current_turn: current_game['turns'].append(current_turn)
                games.append(current_game)
            
            current_game = {
                "game_num": int(m_game.group(1)),
                "total_turns": int(m_game.group(2)),
                "winner": m_game.group(3),
                "turns": []
            }
            current_turn = None
            continue
            
        # Turn Header
        m_turn = turn_header_re.match(line)
        if m_turn:
            if current_turn:
                current_game['turns'].append(current_turn)
            
            # Extract player stats
            # Groups: 1=TurnNum, 2=P1Name, 3=P1HP, 4=P1Cards, 5=P1Lands, 6=P2Name, 7=P2HP, 8=P2Cards, 9=P2Lands, 10=WinProb
            wp_str = m_turn.group(10)
            wp = float(wp_str) if wp_str else 0.5
            current_turn = {
                "turn_num": int(m_turn.group(1)),
                "win_prob": wp,
                "p1": {
                    "name": m_turn.group(2),
                    "hp": int(m_turn.group(3)),
                    "cards": int(m_turn.group(4)),
                    "lands": int(m_turn.group(5)),
                    "board": [] # Will be populated by Board line if present
                },
                "p2": {
                    "name": m_turn.group(6),
                    "hp": int(m_turn.group(7)),
                    "cards": int(m_turn.group(8)),
                    "lands": int(m_turn.group(9)),
                    "board": []
                },
                "actions": []
            }
            continue
            
        # Board State
        m_board = board_re.match(line)
        if m_board and current_turn:
            # Groups: 1=P1Name, 2=P1BoardStr, 3=P2Name, 4=P2BoardStr
            # Parse board string: "Card A, Card B" -> ["Card A", "Card B"]
            p1_b = [x.strip() for x in m_board.group(2).split(',')] if m_board.group(2) != 'empty' else []
            p2_b = [x.strip() for x in m_board.group(4).split(',')] if m_board.group(4) != 'empty' else []
            
            current_turn['p1']['board'] = p1_b
            current_turn['p2']['board'] = p2_b
            continue

        # Log Action — capture ALL events between turn headers
        # (lifelink, prowess, SBA deaths, abilities, tokens, targeting, etc.)
        if current_turn and not line.startswith("---"):
             current_turn['actions'].append(line)
             
    # Append last turn and game
    if current_turn and current_game:
        current_game['turns'].append(current_turn)
    if current_game:
        games.append(current_game)
        
    return {"match_info": match_info, "games": games}
