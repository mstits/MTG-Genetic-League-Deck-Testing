"""scrape_meta — Fetch competitive decklists from public metagame sources.

Scrapers for MTGGoldfish and MTGTop8 metagame pages, plus local .txt/.json
decklist parsing. All scraped decks are formatted as boss deck dicts
compatible with `import_tournament.py` and the league database.

Usage:
    # Fetch top Modern decks from MTGGoldfish
    python scripts/scrape_meta.py --format modern

    # Fetch from MTGTop8
    python scripts/scrape_meta.py --format Modern --source mtgtop8

    # Import a local decklist file
    python scripts/scrape_meta.py --file my_deck.txt

    # Fetch and insert as boss decks into the database
    python scripts/scrape_meta.py --format modern --insert

Rate limits: 1 request/second to be respectful to source servers.
"""

import sys
import os
import re
import json
import time
import argparse
import logging

# Allow running from project root or scripts/
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)) + '/..')

try:
    import requests
except ImportError:
    print("ERROR: 'requests' package required. Install with: pip install requests")
    sys.exit(1)

from data.db import save_deck, get_db_connection

logger = logging.getLogger(__name__)


# ─── MTGGoldfish Scraper ─────────────────────────────────────────────────────

def fetch_mtggoldfish_meta(format_name: str = "modern", limit: int = 15) -> list[dict]:
    """Scrape top decks from MTGGoldfish metagame page.

    Fetches the metagame overview page and individual deck pages to extract
    full decklists. Respects rate limits (1 req/sec).

    Args:
        format_name: MTG format (modern, pioneer, standard, legacy, pauper).
        limit: Maximum number of decks to fetch.

    Returns:
        List of dicts: [{'name': ..., 'colors': ..., 'cards': {name: count}}]
    """
    base_url = f"https://www.mtggoldfish.com/metagame/{format_name}/full"
    headers = {
        "User-Agent": "MTGGeneticLeague/1.0 (deck research; non-commercial)",
        "Accept": "text/html",
    }

    logger.info("Fetching MTGGoldfish %s metagame: %s", format_name, base_url)

    try:
        resp = requests.get(base_url, headers=headers, timeout=30)
        resp.raise_for_status()
    except requests.RequestException as e:
        logger.error("Failed to fetch MTGGoldfish metagame: %s", e)
        return []

    html = resp.text
    decks = []

    # Extract deck links: /archetype/DeckName#paper
    deck_pattern = re.compile(
        r'href="(/archetype/[^"#]+)(?:#[^"]*)?"\s*[^>]*>\s*([^<]+)</a>',
        re.IGNORECASE,
    )
    matches = deck_pattern.findall(html)

    seen_urls = set()
    for url_path, deck_name in matches:
        if url_path in seen_urls:
            continue
        seen_urls.add(url_path)
        deck_name = deck_name.strip()
        if not deck_name or len(deck_name) < 3:
            continue
        if len(decks) >= limit:
            break

        # Fetch individual deck page for the full decklist
        deck_url = f"https://www.mtggoldfish.com{url_path}#paper"
        time.sleep(1)  # Rate limit

        try:
            deck_resp = requests.get(deck_url, headers=headers, timeout=30)
            deck_resp.raise_for_status()
            cards = _parse_mtggoldfish_deck_page(deck_resp.text)
            if cards and sum(cards.values()) >= 40:
                # Detect colors from card data
                colors = _detect_colors_from_name(deck_name)
                decks.append({
                    "name": f"META:{deck_name}",
                    "colors": colors,
                    "cards": cards,
                    "source": "mtggoldfish",
                    "format": format_name,
                })
                logger.info("  ✅ %s (%d cards)", deck_name, sum(cards.values()))
        except requests.RequestException as e:
            logger.warning("  ⚠️  Failed to fetch %s: %s", deck_name, e)
            continue

    logger.info("Fetched %d decks from MTGGoldfish", len(decks))
    return decks


def _parse_mtggoldfish_deck_page(html: str) -> dict[str, int]:
    """Parse a MTGGoldfish deck page to extract card names and quantities.

    Looks for the paper decklist table which has rows like:
        <td class="deck-col-qty">4</td>
        <td class="deck-col-card"><a ...>Lightning Bolt</a></td>
    """
    cards = {}

    # Pattern for paper decklist table rows
    qty_pattern = re.compile(
        r'class="deck-col-qty"[^>]*>\s*(\d+)\s*</td>\s*'
        r'<td[^>]*class="deck-col-card"[^>]*>\s*<a[^>]*>([^<]+)</a>',
        re.IGNORECASE | re.DOTALL,
    )
    for match in qty_pattern.finditer(html):
        qty = int(match.group(1))
        name = match.group(2).strip()
        if name and qty > 0:
            cards[name] = cards.get(name, 0) + qty

    # Fallback: try plaintext download format
    if not cards:
        text_pattern = re.compile(r'^\s*(\d+)\s+(.+?)\s*$', re.MULTILINE)
        for match in text_pattern.finditer(html):
            qty = int(match.group(1))
            name = match.group(2).strip()
            if name and qty > 0 and not name.startswith('<'):
                cards[name] = cards.get(name, 0) + qty

    return cards


# ─── MTGTop8 Scraper ─────────────────────────────────────────────────────────

def fetch_mtgtop8(format_name: str = "Modern", limit: int = 15) -> list[dict]:
    """Scrape recent tournament decks from MTGTop8.

    Args:
        format_name: Format name (Modern, Pioneer, Standard, Legacy, Pauper).
        limit: Maximum decks to fetch.

    Returns:
        List of dicts compatible with boss deck format.
    """
    format_codes = {
        "modern": "MO", "pioneer": "PI", "standard": "ST",
        "legacy": "LE", "vintage": "VI", "pauper": "PAU",
    }

    code = format_codes.get(format_name.lower())
    if not code:
        logger.error("Unknown format for MTGTop8: %s", format_name)
        return []

    url = f"https://www.mtgtop8.com/format?f={code}"
    headers = {
        "User-Agent": "MTGGeneticLeague/1.0 (deck research; non-commercial)",
    }

    logger.info("Fetching MTGTop8 %s decks: %s", format_name, url)

    try:
        resp = requests.get(url, headers=headers, timeout=30)
        resp.raise_for_status()
    except requests.RequestException as e:
        logger.error("Failed to fetch MTGTop8: %s", e)
        return []

    html = resp.text
    decks = []

    # Extract deck event links
    deck_pattern = re.compile(
        r'href="(event\?e=\d+&d=\d+[^"]*)"[^>]*>([^<]+)</a>',
        re.IGNORECASE,
    )
    matches = deck_pattern.findall(html)

    seen = set()
    for url_path, deck_name in matches:
        if url_path in seen:
            continue
        seen.add(url_path)
        deck_name = deck_name.strip()
        if not deck_name or len(deck_name) < 3:
            continue
        if len(decks) >= limit:
            break

        deck_url = f"https://www.mtgtop8.com/{url_path}"
        time.sleep(1)  # Rate limit

        try:
            deck_resp = requests.get(deck_url, headers=headers, timeout=30)
            deck_resp.raise_for_status()
            cards = _parse_mtgtop8_deck_page(deck_resp.text)
            if cards and sum(cards.values()) >= 40:
                colors = _detect_colors_from_name(deck_name)
                decks.append({
                    "name": f"META:{deck_name}",
                    "colors": colors,
                    "cards": cards,
                    "source": "mtgtop8",
                    "format": format_name.lower(),
                })
                logger.info("  ✅ %s (%d cards)", deck_name, sum(cards.values()))
        except requests.RequestException as e:
            logger.warning("  ⚠️  Failed to fetch %s: %s", deck_name, e)
            continue

    logger.info("Fetched %d decks from MTGTop8", len(decks))
    return decks


def _parse_mtgtop8_deck_page(html: str) -> dict[str, int]:
    """Parse an MTGTop8 deck page."""
    cards = {}
    # MTGTop8 uses spans with class "deck_line"
    pattern = re.compile(
        r'class="deck_line"[^>]*>\s*(\d+)\s*<[^>]*>([^<]+)</a>',
        re.IGNORECASE | re.DOTALL,
    )
    for match in pattern.finditer(html):
        qty = int(match.group(1))
        name = match.group(2).strip()
        if name and qty > 0:
            cards[name] = cards.get(name, 0) + qty
    return cards


# ─── Local File Parsing ──────────────────────────────────────────────────────

def import_decklist_file(path: str) -> dict[str, int]:
    """Parse a local .txt decklist (MTGO format: '4 Card Name').

    Also handles:
        - '4x Card Name' format
        - Lines starting with '//' (comments, sideboard marker)
        - Blank lines
        - JSON files with {card_name: count} format

    Args:
        path: Path to the decklist file.

    Returns:
        Dict of card_name → count.
    """
    if not os.path.exists(path):
        raise FileNotFoundError(f"Decklist not found: {path}")

    with open(path, 'r') as f:
        content = f.read()

    # Try JSON first
    if path.endswith('.json'):
        try:
            data = json.loads(content)
            if isinstance(data, dict):
                return {str(k): int(v) for k, v in data.items()}
        except (json.JSONDecodeError, ValueError):
            pass

    # Parse text format
    cards = {}
    in_sideboard = False

    for line in content.splitlines():
        line = line.strip()
        if not line or line.startswith('//'):
            continue
        if line.lower().startswith('sideboard'):
            in_sideboard = True
            continue
        if in_sideboard:
            continue  # Skip sideboard for now

        # Match "4 Card Name" or "4x Card Name"
        match = re.match(r'^(\d+)\s*x?\s+(.+)$', line)
        if match:
            qty = int(match.group(1))
            name = match.group(2).strip()
            cards[name] = cards.get(name, 0) + qty
        elif len(line) > 2:
            # Just a card name (1 copy)
            cards[line] = cards.get(line, 0) + 1

    return cards


# ─── Utilities ────────────────────────────────────────────────────────────────

def _detect_colors_from_name(deck_name: str) -> str:
    """Best-effort color detection from deck name.

    Returns a color string like 'RW', 'UBR', etc.
    """
    name_lower = deck_name.lower()
    colors = ""

    color_keywords = {
        "W": ["white", "azorius", "orzhov", "selesnya", "boros", "esper",
               "jeskai", "abzan", "mardu", "naya", "bant", "4c", "5c",
               "death and taxes", "hammer", "humans", "soldiers"],
        "U": ["blue", "azorius", "dimir", "simic", "izzet", "esper",
               "jeskai", "sultai", "grixis", "temur", "bant", "4c", "5c",
               "merfolk", "control", "murktide", "delver"],
        "B": ["black", "orzhov", "dimir", "golgari", "rakdos", "esper",
               "sultai", "abzan", "grixis", "mardu", "jund", "4c", "5c",
               "shadow", "scam", "coffers"],
        "R": ["red", "boros", "izzet", "gruul", "rakdos", "jeskai",
               "temur", "mardu", "naya", "jund", "4c", "5c",
               "burn", "prowess", "storm"],
        "G": ["green", "selesnya", "simic", "golgari", "gruul", "sultai",
               "temur", "abzan", "naya", "jund", "bant", "4c", "5c",
               "tron", "titan", "scales", "elves"],
    }

    for color, keywords in color_keywords.items():
        if any(kw in name_lower for kw in keywords):
            if color not in colors:
                colors += color

    return colors if colors else "C"  # Colorless fallback


def update_boss_decks(
    format_name: str = "modern",
    source: str = "mtggoldfish",
    limit: int = 10,
    insert: bool = False,
) -> list[dict]:
    """Fetch meta decks and optionally insert as boss decks.

    Args:
        format_name: Target format.
        source: 'mtggoldfish' or 'mtgtop8'.
        limit: Max decks to fetch.
        insert: If True, insert into the league database.

    Returns:
        List of deck dicts.
    """
    if source == "mtgtop8":
        decks = fetch_mtgtop8(format_name, limit)
    else:
        decks = fetch_mtggoldfish_meta(format_name, limit)

    if insert and decks:
        logger.info("Inserting %d meta decks into database...", len(decks))
        for deck in decks:
            try:
                deck_id = save_deck(
                    name=deck["name"],
                    card_list=deck["cards"],
                    generation=0,
                    colors=deck.get("colors", ""),
                )
                logger.info("  Inserted: %s (ID: %s)", deck["name"], deck_id)
            except Exception as e:
                logger.warning("  Failed to insert %s: %s", deck["name"], e)

    return decks


# ─── CLI ──────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="Fetch competitive MTG decklists from online sources.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python scripts/scrape_meta.py --format modern
  python scripts/scrape_meta.py --format modern --source mtgtop8
  python scripts/scrape_meta.py --file deck.txt
  python scripts/scrape_meta.py --format modern --insert --limit 5
        """,
    )
    parser.add_argument("--format", default="modern",
                        help="MTG format (modern, pioneer, standard, legacy, pauper)")
    parser.add_argument("--source", default="mtggoldfish",
                        choices=["mtggoldfish", "mtgtop8"],
                        help="Data source to scrape")
    parser.add_argument("--limit", type=int, default=10,
                        help="Max decks to fetch (default: 10)")
    parser.add_argument("--file", type=str,
                        help="Parse a local decklist file instead of scraping")
    parser.add_argument("--insert", action="store_true",
                        help="Insert fetched decks into the league database")
    parser.add_argument("--json", action="store_true",
                        help="Output results as JSON")

    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
    )

    if args.file:
        # Parse local file
        try:
            cards = import_decklist_file(args.file)
            result = {
                "name": os.path.basename(args.file),
                "cards": cards,
                "total": sum(cards.values()),
            }
            if args.json:
                print(json.dumps(result, indent=2))
            else:
                print(f"\n📄 Parsed {result['total']} cards from {args.file}:")
                for name, qty in sorted(cards.items(), key=lambda x: -x[1]):
                    print(f"  {qty}x {name}")

            if args.insert:
                deck_id = save_deck(
                    name=f"IMPORT:{result['name']}",
                    card_list=cards,
                )
                print(f"\n✅ Inserted as deck ID: {deck_id}")
        except FileNotFoundError as e:
            print(f"❌ {e}")
            sys.exit(1)
    else:
        # Scrape from online source
        decks = update_boss_decks(
            format_name=args.format,
            source=args.source,
            limit=args.limit,
            insert=args.insert,
        )

        if args.json:
            print(json.dumps(decks, indent=2))
        else:
            print(f"\n🏆 Fetched {len(decks)} {args.format} decks from {args.source}:")
            for d in decks:
                total = sum(d["cards"].values())
                print(f"  • {d['name']} ({d.get('colors', '?')}) — {total} cards")

            if not decks:
                print("  No decks found. The source site may have changed its layout.")
                print("  Try --source mtgtop8 as an alternative.")


if __name__ == "__main__":
    main()
