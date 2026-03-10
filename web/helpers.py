"""Shared helpers used across route modules.

These utilities live here (not in app.py) to avoid circular imports when
route modules need to import from the main app. Functions that were originally
defined inline in app.py are extracted here as the monolith is modularized.
"""

import re
import time
import logging

logger = logging.getLogger(__name__)

# ─── Rate Limiting ────────────────────────────────────────────────────────────
# Simple in-memory rate limiter for CPU-intensive simulation endpoints.
# In production, replace with Redis-backed limiting.

_rate_limit_store: dict[str, list[float]] = {}


def check_rate_limit(client_ip: str, window_seconds: int = 60, max_requests: int = 3) -> bool:
    """Check if a client IP has exceeded the rate limit.

    Args:
        client_ip: The client's IP address
        window_seconds: Time window in seconds (default: 60)
        max_requests: Max requests allowed per window (default: 3)

    Returns:
        True if request is allowed, False if rate limited.
    """
    now = time.time()
    if client_ip not in _rate_limit_store:
        _rate_limit_store[client_ip] = []

    # Prune timestamps outside the window
    _rate_limit_store[client_ip] = [
        t for t in _rate_limit_store[client_ip]
        if now - t < window_seconds
    ]

    if len(_rate_limit_store[client_ip]) >= max_requests:
        return False

    _rate_limit_store[client_ip].append(now)
    return True


# ─── Decklist Parser ──────────────────────────────────────────────────────────

def parse_decklist(raw: str) -> dict:
    """Parse a user-submitted decklist from multiple common formats.

    Supports:
        4 Lightning Bolt         — count + name
        4x Lightning Bolt        — count with 'x' separator
        Lightning Bolt x4        — name then count
        4 Lightning Bolt (M20) 123  — Arena export with set code + collector #
        // comment or # comment  — ignored
        Sideboard                — stops parsing (sideboard not imported)

    Returns:
        dict mapping card_name -> count (e.g. {"Lightning Bolt": 4})
    """
    cards = {}
    for line in raw.strip().splitlines():
        line = line.strip()
        if not line or line.startswith('#') or line.startswith('//') or line.lower().startswith('sideboard'):
            continue

        # Try "4 Card Name" or "4x Card Name" (with optional Arena set/collector suffix)
        m = re.match(r'^(\d+)x?\s+(.+?)(?:\s*\([\w\d]+\)\s*\d+)?$', line)
        if m:
            count = int(m.group(1))
            name = m.group(2).strip()
            cards[name] = cards.get(name, 0) + count
            continue

        # Try "Card Name x4"
        m = re.match(r'^(.+?)\s+x?(\d+)$', line)
        if m:
            name = m.group(1).strip()
            count = int(m.group(2))
            cards[name] = cards.get(name, 0) + count
            continue

        # Just a card name (1 copy)
        if len(line) > 2:
            cards[line] = cards.get(line, 0) + 1

    return cards
