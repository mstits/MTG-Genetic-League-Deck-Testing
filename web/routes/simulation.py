"""Simulation routes — deck testing, flex optimization, gauntlet, and analysis.

These endpoints are CPU-intensive: they run actual MTG game simulations to
evaluate decklists. Most are rate-limited to prevent abuse.

Endpoints:
    POST /api/test-deck       — Test a decklist against top league opponents
    POST /api/flex-test       — Optimize flex slots via hypergeometric analysis
    POST /api/mana-calc       — Analyze mana base using Frank Karsten's math
    POST /api/mulligan-eval   — Evaluate opening hand with Mulligan AI
    GET  /api/gauntlet/eras   — List available historical eras
    POST /api/gauntlet/run    — Run deck against historical era Top 8
    POST /api/salt-score      — Calculate Commander salt score and bracket
    GET  /api/mutations/heatmap — Top card swaps ranked by ELO delta
"""

import os
import json
import logging
from typing import List, Optional

from fastapi import APIRouter, Request
from starlette.responses import JSONResponse
from pydantic import BaseModel
from data.db import get_db_connection, get_mutation_heatmap

from web.helpers import check_rate_limit, parse_decklist

logger = logging.getLogger(__name__)

router = APIRouter(tags=["simulation"])


# ─── Request Models ───────────────────────────────────────────────────────────

class MulliganRequest(BaseModel):
    """Request body for mulligan evaluation endpoint."""
    deck_id: int
    hand: List[str]
    mulligan_count: int = 0
    meta_archetype: str = "Midrange"


# ─── Deck Testing ────────────────────────────────────────────────────────────

@router.post("/api/test-deck")
async def test_deck(request: Request):
    """Test a user-submitted decklist against top league decks.

    Runs Bo3 game simulations using the HeuristicAgent for both sides.
    Results include per-matchup breakdown, overall win rate, letter grade,
    and automatic enrollment into the league for evolution.

    Rate limited to 3 requests per minute per IP (CPU-intensive).

    Request body:
        decklist: str — decklist text (supports Arena, MTGO, and plain formats)
        opponent_id: int (optional) — test against a specific deck (5 games)

    Returns:
        cards_submitted/cards_valid: int — parsing results
        invalid_cards: list[str] — unrecognized card names
        win_rate: float — overall win percentage
        record: str — "NW-NL-ND" format
        matchups: list[dict] — per-opponent results
        grade: str — S/A/B/C/D based on win rate
        enrolled_id: int — league ID if auto-enrolled
    """
    client_ip = request.client.host if request.client else "unknown"
    if not check_rate_limit(client_ip):
        return JSONResponse(
            {"error": "Rate limit exceeded. Please wait 60 seconds before testing again."},
            status_code=429
        )
    import traceback
    try:
        try:
            body = await request.json()
            raw_decklist = body.get('decklist', '')
        except (ValueError, TypeError):
            form = await request.form()
            raw_decklist = form.get('decklist', '')

        if not raw_decklist or not raw_decklist.strip():
            return JSONResponse({"error": "Empty decklist"}, status_code=400)

        cards = parse_decklist(raw_decklist)
        if not cards:
            return JSONResponse({"error": "Could not parse any cards"}, status_code=400)

        total_cards = sum(cards.values())

        # Load card pool from module-level cache in app.py
        from web.cache import get_card_pool
        card_pool = get_card_pool()

        # Validate cards against the pool
        valid = {}
        invalid = []
        for name, count in cards.items():
            if name in card_pool:
                valid[name] = count
            else:
                invalid.append(name)

        if not valid:
            return JSONResponse({"error": "No valid cards found", "invalid_cards": invalid}, status_code=400)

        # Build deck from validated cards
        from engine.card_builder import dict_to_card
        from engine.deck import Deck
        from engine.game import Game
        from engine.player import Player
        from simulation.runner import SimulationRunner
        from agents.heuristic_agent import HeuristicAgent

        def make_deck(card_dict):
            """Build a Deck object from a card_name->count dict."""
            deck = Deck()
            for name, count in card_dict.items():
                try:
                    data = card_pool.get(name)
                    if data:
                        card = dict_to_card(data)
                        deck.add_card(card, count)
                except Exception as e:
                    logger.warning("Skipping corrupt card '%s': %s", name, e)
                    traceback.print_exc()
            return deck

        user_deck = make_deck(valid)

        # Get opponents — either a specific deck or top 10 by ELO
        opponent_id = body.get('opponent_id')

        with get_db_connection() as conn:
            cursor = conn.cursor()
            if opponent_id:
                cursor.execute('''
                    SELECT id, name, card_list, elo FROM decks 
                    WHERE id = %s AND active=1
                ''', (int(opponent_id),))
            else:
                cursor.execute('''
                    SELECT id, name, card_list, elo FROM decks 
                    WHERE active=1 ORDER BY elo DESC LIMIT 10
                ''')
            opponents = [dict(row) for row in cursor.fetchall()]

        if not opponents:
            return JSONResponse({"error": "No opponents found"}, status_code=400)

        # Run matchups in parallel threads for speed
        from concurrent.futures import ThreadPoolExecutor, as_completed

        def run_matchup(opp):
            """Run a Bo3 matchup against one opponent. Thread-safe: fresh deck per thread."""
            try:
                opp_cards = json.loads(opp['card_list'])
                if isinstance(opp_cards, list):
                    c = {}
                    for n in opp_cards: c[n] = c.get(n, 0) + 1
                    opp_cards = c
                opp_deck = make_deck(opp_cards)
            except Exception as e:
                logger.warning("Skipping opponent '%s' due to deck error: %s", opp['name'], e)
                return None

            thread_user_deck = make_deck(valid)
            num_games = 5 if opponent_id else 3

            w, l, d = 0, 0, 0
            turns_list = []
            per_game = []
            for g_idx in range(num_games):
                try:
                    p1 = Player("UserDeck", thread_user_deck)
                    p2 = Player(opp['name'], opp_deck)
                    game = Game([p1, p2])
                    runner = SimulationRunner(game, [HeuristicAgent(), HeuristicAgent()])
                    result = runner.run()
                    turns_list.append(result.turns)
                    game_winner = None
                    if result.winner == "UserDeck":
                        w += 1
                        game_winner = "You"
                    elif result.winner == opp['name']:
                        l += 1
                        game_winner = opp['name']
                    else:
                        d += 1
                        game_winner = "Draw"
                    per_game.append({"game": g_idx + 1, "winner": game_winner, "turns": result.turns})
                    if not opponent_id and (w >= 2 or l >= 2):
                        break
                except Exception as e:
                    logger.warning("Simulation failed vs %s: %s", opp['name'], e)
                    d += 1

            match_result = "Win" if w > l else "Loss" if l > w else "Draw"
            result_dict = {
                "opponent": opp['name'],
                "opponent_elo": round(opp['elo']),
                "games": f"{w}-{l}" + (f"-{d}" if d else ""),
                "result": match_result,
                "avg_turns": round(sum(turns_list) / max(len(turns_list), 1), 1)
            }
            if opponent_id:
                result_dict["per_game"] = per_game
            return result_dict

        results = []
        total_wins, total_losses, total_draws = 0, 0, 0

        with ThreadPoolExecutor(max_workers=min(len(opponents), 4)) as executor:
            futures = {executor.submit(run_matchup, opp): opp for opp in opponents}
            for future in as_completed(futures):
                matchup = future.result()
                if matchup is None:
                    continue
                results.append(matchup)
                if matchup["result"] == "Win":
                    total_wins += 1
                elif matchup["result"] == "Loss":
                    total_losses += 1
                else:
                    total_draws += 1

        results.sort(key=lambda r: r['opponent_elo'], reverse=True)

        total_played = total_wins + total_losses + total_draws
        win_rate = round(total_wins / max(total_played, 1) * 100, 1)
        grade = "S" if win_rate >= 80 else "A" if win_rate >= 60 else "B" if win_rate >= 40 else "C" if win_rate >= 20 else "D"

        # Auto-enroll the tested deck into the league
        enrolled_id = None
        deck_name = None
        try:
            deck_colors = set()
            for name in valid:
                cd = card_pool.get(name, {})
                for c in cd.get('color_identity', []):
                    deck_colors.add(c)
            colors_str = ''.join(sorted(deck_colors, key=lambda c: 'WUBRG'.index(c) if c in 'WUBRG' else 99))

            import hashlib
            deck_hash = hashlib.md5(json.dumps(valid, sort_keys=True).encode()).hexdigest()[:4]
            deck_name = f"User-{colors_str or 'C'}-{deck_hash}"

            from data.db import save_deck
            enrolled_id = save_deck(deck_name, valid, generation=0, colors=colors_str)
            logger.info("Enrolled test deck as '%s' (ID: %s)", deck_name, enrolled_id)
        except Exception as enroll_err:
            logger.warning("Failed to enroll deck: %s", enroll_err)

        return JSONResponse({
            "cards_submitted": total_cards,
            "cards_valid": sum(valid.values()),
            "invalid_cards": invalid,
            "win_rate": win_rate,
            "record": f"{total_wins}W-{total_losses}L-{total_draws}D",
            "matchups": results,
            "grade": grade,
            "enrolled_id": enrolled_id,
            "enrolled_name": deck_name if enrolled_id else None
        })
    except Exception as e:
        logger.exception("Unhandled error in %s", request.url.path)
        return JSONResponse({"error": "Internal server error. Please try again."}, status_code=500)


# ─── Flex Slot Optimization ──────────────────────────────────────────────────

@router.post("/api/flex-test")
async def flex_test(request: Request):
    """Test flex slots against Gauntlet bosses finding mathematically optimal configs.

    Takes a core decklist (< 60 cards) and a pool of candidate flex cards,
    then evaluates each configuration using the simulation engine.

    Rate limited to 3 requests per minute per IP.
    """
    client_ip = request.client.host if request.client else "unknown"
    if not check_rate_limit(client_ip):
        return JSONResponse({"error": "Rate limit exceeded. Please wait 60 seconds."}, status_code=429)

    try:
        body = await request.json()
        raw_core = body.get('core_decklist', '')
        raw_flex = body.get('flex_pool', '')
    except Exception as e:
        logger.debug("Failed to parse flex-test JSON body: %s", e)
        return JSONResponse({"error": "Invalid JSON mapping"}, status_code=400)

    core_cards = parse_decklist(raw_core)
    flex_pool = list(parse_decklist(raw_flex).keys())

    if sum(core_cards.values()) > 59:
        return JSONResponse(
            {"error": f"Core deck already {sum(core_cards.values())} cards. Must be < 60 to have flex slots."},
            status_code=400
        )
    if not flex_pool:
        return JSONResponse({"error": "Flex pool cannot be empty."}, status_code=400)

    from simulation.flex_tester import FlexTester
    try:
        tester = FlexTester(core_deck=core_cards, flex_pool=flex_pool)
        results = tester.run_tests()
        return JSONResponse({"results": results})
    except ValueError as e:
        return JSONResponse({"error": str(e)}, status_code=400)
    except Exception as e:
        logger.exception("Flex test error")
        return JSONResponse({"error": "Internal server error. Please try again."}, status_code=500)


# ─── Mana Base Analysis ──────────────────────────────────────────────────────

@router.post("/api/mana-calc")
async def mana_calc(request: Request):
    """Analyze decklist mana base using Frank Karsten's hypergeometric math.

    Evaluates whether the mana base supports the deck's color requirements
    by computing probability of having the right colors on curve.
    """
    try:
        body = await request.json()
        raw_decklist = body.get('decklist', '')
    except Exception as e:
        logger.debug("Failed to parse mana-calc JSON body: %s", e)
        return JSONResponse({"error": "Invalid JSON"}, status_code=400)

    deck_dict = parse_decklist(raw_decklist)
    if not deck_dict:
        return JSONResponse({"error": "Empty or invalid decklist."}, status_code=400)

    # Use module-level cached card pool (loaded once, not from disk per-request)
    from web.cache import get_card_pool
    card_pool = get_card_pool()

    from utils.hypergeometric import evaluate_deck_mana
    try:
        results = evaluate_deck_mana(deck_dict, card_pool)
        return JSONResponse({"results": results})
    except Exception as e:
        logger.exception("Mana calc error")
        return JSONResponse({"error": "Internal server error. Please try again."}, status_code=500)


# ─── Mulligan Evaluation ─────────────────────────────────────────────────────

@router.post("/api/mulligan-eval")
async def evaluate_mulligan(req: MulliganRequest):
    """Evaluate an opening hand using the Mulligan AI.

    The AI uses a combination of learned model weights and heuristic analysis
    to predict the expected "goldfish turn" (turn the hand would win uncontested)
    and recommends keep/mulligan with explanations.

    Request body (JSON):
        deck_id: int — league deck ID to evaluate against
        hand: list[str] — card names in the opening hand
        mulligan_count: int — number of mulligans taken so far (default: 0)
        meta_archetype: str — expected opponent type (default: "Midrange")
    """
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM decks WHERE id = %s", (req.deck_id,))
        row = cursor.fetchone()

    if not row:
        return JSONResponse({"error": "Deck not found"}, status_code=404)

    from engine.deck import Deck
    from engine.card_builder import dict_to_card
    from web.cache import get_card_search_cache

    deck = Deck()
    cache = get_card_search_cache()
    pool = {c['name']: c for c in cache}

    decklist = json.loads(row['card_list'])
    if isinstance(decklist, list):
        counts = {}
        for n in decklist: counts[n] = counts.get(n, 0) + 1
        decklist = counts

    for name, count in decklist.items():
        if name in pool:
            deck.add_card(dict_to_card(pool[name]), count)

    deck.db_id = row['id']
    deck.elo = row['elo']

    hand_cards = []
    if not req.hand:
        import random
        deck_list = deck.get_game_deck()
        random.shuffle(deck_list)
        hand_cards = deck_list[:7]
    else:
        for name in req.hand:
            if name in pool:
                hand_cards.append(dict_to_card(pool[name]))

    model_path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'data', 'mulligan_model.npz')
    if not os.path.exists(model_path):
        model_path = None

    from agents.mulligan_ai import MulliganAI
    ai = MulliganAI(model_path=model_path)

    expected_turn = ai.evaluate_hand(hand_cards, deck)
    heuristic_turn = ai.heuristic_goldfish_turn(hand_cards)

    should_mull, explanation = ai.should_mulligan(hand_cards, deck, req.mulligan_count, req.meta_archetype)
    recommendation = "Mulligan" if should_mull else "Keep"

    return {
        "expected_win_turn": round(float(expected_turn), 2),
        "heuristic_win_turn": round(float(heuristic_turn), 2),
        "recommendation": recommendation,
        "explanation": explanation,
        "hand": [c.name for c in hand_cards]
    }


# ─── Historical Gauntlet ("Time Machine") ────────────────────────────────────

@router.get("/api/gauntlet/eras")
async def get_gauntlet_eras():
    """List available historical eras for the Time Machine feature.

    Each era represents a tournament format from MTG history (e.g., "Invasion Block",
    "Ravnica Standard") with pre-built Top 8 decklists to test against.
    """
    from league.historical_gauntlet import get_era_list
    return {"eras": get_era_list()}


@router.post("/api/gauntlet/run")
async def run_gauntlet_endpoint(request: Request):
    """Run a user deck against a historical era's Top 8.

    Simulates matches against all 8 decks from the selected era and returns
    results including individual matchup breakdowns.

    Request body:
        decklist: str — decklist in any supported format
        era: str — era identifier from /api/gauntlet/eras
    """
    data = await request.json()
    decklist_raw = data.get("decklist", "")
    era_id = data.get("era", "")

    if not decklist_raw or not era_id:
        return JSONResponse({"error": "Both 'decklist' and 'era' required"}, status_code=400)

    if isinstance(decklist_raw, str):
        parsed = parse_decklist(decklist_raw)
    else:
        parsed = decklist_raw

    if not parsed:
        return JSONResponse({"error": "Could not parse decklist"}, status_code=400)

    from league.historical_gauntlet import run_gauntlet
    result = run_gauntlet(parsed, era_id)
    return result


# ─── Mutation Heatmaps ───────────────────────────────────────────────────────

@router.get("/api/mutations/heatmap")
async def get_heatmap(limit: int = 50):
    """Get top card swaps ranked by average ELO delta.

    Shows which genetic mutations (card-in/card-out pairs) have historically
    produced the largest ELO improvements, helping identify strong replacements.

    Args:
        limit: Maximum number of mutations to return (default: 50)
    """
    data = get_mutation_heatmap(limit)
    return {"mutations": data, "total": len(data)}


# ─── Salt Score (Commander Brackets) ─────────────────────────────────────────

@router.post("/api/salt-score")
async def get_salt_score(request: Request):
    """Calculate Commander salt score and bracket for a decklist.

    Uses EDHREC salt ratings and card category analysis to determine
    how "salty" a Commander deck is (1-10 scale) and which Commander
    bracket it falls into.
    """
    data = await request.json()
    decklist_raw = data.get("decklist", "")

    if isinstance(decklist_raw, str):
        parsed = parse_decklist(decklist_raw)
    else:
        parsed = decklist_raw

    if not parsed:
        return JSONResponse({"error": "Could not parse decklist"}, status_code=400)

    from engine.salt_score import calculate_salt_score
    result = calculate_salt_score(parsed)
    return result
