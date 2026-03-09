"""Mechanics Registry — Plugin system for keyword abilities and effects.

Instead of adding every new mechanic to card.py (5700+ LOC), register them
here as self-contained handlers. Each mechanic module registers itself via
@register_mechanic or @register_keyword.

Usage:
    # In engine/mechanics/cascade.py:
    from engine.mechanics import register_keyword

    @register_keyword("cascade")
    def apply_cascade(card, game):
        ...

    # In engine/card.py __post_init__ or elsewhere:
    from engine.mechanics import get_keyword_handler
    handler = get_keyword_handler("cascade")
    if handler:
        handler(card, game)
"""

from typing import Callable, Optional

# Global registries
_keyword_handlers: dict[str, Callable] = {}
_mechanic_parsers: dict[str, Callable] = {}
_trigger_handlers: dict[str, Callable] = {}


def register_keyword(keyword: str) -> Callable:
    """Decorator to register a keyword ability handler.
    
    Example:
        @register_keyword("flying")
        def apply_flying(card, game):
            card.has_flying = True
    """
    def decorator(fn: Callable) -> Callable:
        _keyword_handlers[keyword.lower()] = fn
        return fn
    return decorator


def register_mechanic(name: str) -> Callable:
    """Decorator to register a mechanic parser (for oracle text parsing).
    
    Example:
        @register_mechanic("cascade")
        def parse_cascade(card, oracle_text):
            if "cascade" in oracle_text.lower():
                card.has_cascade = True
    """
    def decorator(fn: Callable) -> Callable:
        _mechanic_parsers[name.lower()] = fn
        return fn
    return decorator


def register_trigger(trigger_type: str) -> Callable:
    """Decorator to register a trigger handler.
    
    Example:
        @register_trigger("landfall")
        def handle_landfall(card, game, land):
            ...
    """
    def decorator(fn: Callable) -> Callable:
        _trigger_handlers[trigger_type.lower()] = fn
        return fn
    return decorator


def get_keyword_handler(keyword: str) -> Optional[Callable]:
    """Get the handler for a keyword ability, if registered."""
    return _keyword_handlers.get(keyword.lower())


def get_mechanic_parser(name: str) -> Optional[Callable]:
    """Get the parser for a mechanic, if registered."""
    return _mechanic_parsers.get(name.lower())


def get_trigger_handler(trigger_type: str) -> Optional[Callable]:
    """Get the handler for a trigger type, if registered."""
    return _trigger_handlers.get(trigger_type.lower())


def all_keywords() -> list[str]:
    """Return all registered keyword names."""
    return list(_keyword_handlers.keys())


def all_mechanics() -> list[str]:
    """Return all registered mechanic names."""
    return list(_mechanic_parsers.keys())


def discover_mechanics() -> None:
    """Auto-discover and import all mechanic modules in this package."""
    import importlib
    import pkgutil
    import os
    
    pkg_dir = os.path.dirname(__file__)
    for _, module_name, _ in pkgutil.iter_modules([pkg_dir]):
        if module_name.startswith('_'):
            continue
        importlib.import_module(f'.{module_name}', package=__name__)
