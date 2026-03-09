"""Combat keyword mechanics — evasion, blocking restrictions, protection.

These were previously parsed inline in card.py. Registering them here
provides a single source of truth and makes it trivial to add new keywords.
"""

from engine.mechanics import register_keyword


@register_keyword("flying")
def apply_flying(card, game=None):
    """Flying creatures can only be blocked by creatures with flying or reach."""
    card.has_flying = True


@register_keyword("trample")
def apply_trample(card, game=None):
    """Excess combat damage dealt to blocking creatures carries over to the defending player."""
    card.has_trample = True


@register_keyword("first_strike")
def apply_first_strike(card, game=None):
    """Deals combat damage before creatures without first strike."""
    card.has_first_strike = True


@register_keyword("double_strike")
def apply_double_strike(card, game=None):
    """Deals combat damage in both the first-strike and normal damage steps."""
    card.has_double_strike = True


@register_keyword("deathtouch")
def apply_deathtouch(card, game=None):
    """Any amount of damage this creature deals is enough to destroy the target."""
    card.has_deathtouch = True


@register_keyword("lifelink")
def apply_lifelink(card, game=None):
    """Damage dealt by this creature also heals its controller."""
    card.has_lifelink = True


@register_keyword("vigilance")
def apply_vigilance(card, game=None):
    """Attacking doesn't cause this creature to tap."""
    card.has_vigilance = True


@register_keyword("haste")
def apply_haste(card, game=None):
    """This creature can attack and tap the turn it comes under your control."""
    card.has_haste = True


@register_keyword("hexproof")
def apply_hexproof(card, game=None):
    """This creature can't be the target of spells or abilities opponents control."""
    card.has_hexproof = True


@register_keyword("indestructible")
def apply_indestructible(card, game=None):
    """Damage and effects that say 'destroy' don't destroy this permanent."""
    card.has_indestructible = True


@register_keyword("menace")
def apply_menace(card, game=None):
    """This creature can't be blocked except by two or more creatures."""
    card.has_menace = True


@register_keyword("reach")
def apply_reach(card, game=None):
    """This creature can block creatures with flying."""
    card.has_reach = True


@register_keyword("ward")
def apply_ward(card, game=None):
    """Whenever this permanent becomes the target of a spell or ability an opponent controls,
    counter it unless that player pays the ward cost."""
    card.has_ward = True


@register_keyword("prowess")
def apply_prowess(card, game=None):
    """Whenever you cast a noncreature spell, this creature gets +1/+1 until end of turn."""
    card.has_prowess = True


@register_keyword("flash")
def apply_flash(card, game=None):
    """You may cast this spell any time you could cast an instant."""
    card.has_flash = True


@register_keyword("undying")
def apply_undying(card, game=None):
    """When this creature dies, if it had no +1/+1 counters, return it with a +1/+1 counter."""
    card.has_undying = True


@register_keyword("persist")
def apply_persist(card, game=None):
    """When this creature dies, if it had no -1/-1 counters, return it with a -1/-1 counter."""
    card.has_persist = True
