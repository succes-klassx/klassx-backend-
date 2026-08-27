"""
Pricing (spec: prices not finalized when specced, now confirmed by the
product owner — same rate across all subjects, billed per hour of session
length).

Kept in its own module (rather than inline in views.py) so both the
serializers (to display prices) and the views (to charge the right amount
via Stripe) can import it without a circular-import issue.

Rates live in the `PricingRate` DB table (editable from the Django admin,
no code deploy needed to change a price) — `_DEFAULT_RATES_CENTS` below is
only a fallback, used for any tier that doesn't have a row yet, so the
system works correctly out of the box before an admin has touched
anything. Run `python manage.py seed_pricing` to create the initial rows
matching these defaults.
"""

# Per-hour rate in cents (EUR), by group tier — identical for every subject.
# Fallback only — see module docstring. The real, editable values live in
# the PricingRate table.
_DEFAULT_RATES_CENTS = {
    "GROUP_10": 900,     # 9€/h
    "GROUP_8": 1200,     # 12€/h
    "GROUP_6": 1500,     # 15€/h
    "GROUP_5": 1800,     # 18€/h
    "GROUP_4": 2200,     # 22€/h
    "GROUP_3": 2500,     # 25€/h
    "GROUP_2": 3500,     # 35€/h
    "INDIVIDUAL": 5500,  # 55€/h
}

# Per-hour rate in millimes (1 DT = 1000 millimes), by group tier — market
# specific to Tunisia (Konnect — see core/services/konnect.py), set
# directly rather than converted from the EUR rates above. Fallback only —
# see module docstring; the real, editable values live in PricingRate.
_DEFAULT_RATES_MILLIMES_TND = {
    "GROUP_10": 12000,   # 12 DT/h
    "GROUP_8": 16000,    # 16 DT/h
    "GROUP_6": 20000,    # 20 DT/h
    "GROUP_5": 24000,    # 24 DT/h
    "GROUP_4": 25000,    # 25 DT/h
    "GROUP_3": 40000,    # 40 DT/h
    "GROUP_2": 60000,    # 60 DT/h
    "INDIVIDUAL": 80000, # 80 DT/h
}


def _rates_by_tier():
    """DB rates, with the hardcoded defaults filled in for any tier missing a row."""
    from .models import PricingRate
    db_rates = dict(PricingRate.objects.values_list("group_tier", "price_per_hour_cents"))
    return {**_DEFAULT_RATES_CENTS, **db_rates}


def _rates_by_tier_tnd_millimes():
    """Same as _rates_by_tier() but the Tunisia/TND rates (in millimes) — see PricingRate.price_per_hour_millimes_tnd."""
    from .models import PricingRate
    db_rates = {
        tier: millimes
        for tier, millimes in PricingRate.objects.values_list("group_tier", "price_per_hour_millimes_tnd")
        if millimes  # a row with 0 (unset) falls back to the default, doesn't override it
    }
    return {**_DEFAULT_RATES_MILLIMES_TND, **db_rates}


def rate_per_hour_cents(group_tier):
    """Public lookup for a single tier's current per-hour rate — see module docstring for where it comes from."""
    return _rates_by_tier().get(group_tier, 0)


def rate_per_hour_tnd(group_tier):
    """
    Tunisia-specific per-hour rate, in dinars (not millimes) — for display
    to students in Tunisia (Konnect payment flow, see
    core/services/konnect.py). Set independently per tier, NOT a currency
    conversion of the EUR rate — see PricingRate/module docstrings.
    """
    return _rates_by_tier_tnd_millimes().get(group_tier, 0) / 1000


def session_price_cents(class_session):
    """Price for one specific session = hourly rate x its duration in hours."""
    rate = _rates_by_tier().get(class_session.group_tier, 0)
    duration_hours = (class_session.end_time - class_session.start_time).total_seconds() / 3600
    return round(rate * duration_hours)


def session_price_millimes_tnd(class_session):
    """Tunisia/Konnect equivalent of session_price_cents — see rate_per_hour_tnd."""
    rate = _rates_by_tier_tnd_millimes().get(class_session.group_tier, 0)
    duration_hours = (class_session.end_time - class_session.start_time).total_seconds() / 3600
    return round(rate * duration_hours)


# Recurring, package-based specialty subscriptions (spec: confirmed with
# product owner — students pick a weekly-hour package per subject, billed
# monthly with automatic renewal; the INDIVIDUAL plan has no package and
# stays pay-per-session with no commitment). A month is simplified to a
# flat 4 weeks for pricing purposes: some months have 4 occurrences of a
# given weekday, some have 5, but a consistent flat monthly amount is
# simpler and more predictable for families than a variable one. Revisit if
# this simplification turns out to matter financially.
SESSIONS_PER_MONTH = 4

# Package values are stored as WEEKLY hours (what actually gets scheduled
# as a recurring slot — see GroupAssignmentViewSet.schedule), and billed
# monthly as weekly_hours x SESSIONS_PER_MONTH. The two lightest packages
# (1h/semaine, 2h/semaine) are deliberately *labelled* by their monthly
# total instead — "4h/mois" and "8h/mois" — since at that size a monthly
# framing reads more naturally than "1h/semaine"; the underlying math is
# identical either way (1 x 4 = 4, 2 x 4 = 8). Always use
# WEEKLY_HOURS_LABELS (or GroupRequest.get_weekly_hours_display() on the
# backend) to display a package — never hand-build a "Xh/semaine" string
# from the raw value, since that breaks for these two.
WEEKLY_HOURS_PACKAGES = [4, 6, 8, 12, 16]

WEEKLY_HOURS_LABELS = {
    4: "1h/semaine (4h/mois)",
    6: "1,5h/semaine (6h/mois)",
    8: "2h/semaine (8h/mois)",
    12: "3h/semaine (12h/mois)",
    16: "4h/semaine (16h/mois)",
}


def package_monthly_price_cents(group_tier, weekly_hours):
    """Monthly price for a weekly-hour package at a given group tier."""
    rate = _rates_by_tier().get(group_tier, 0)
    return round(rate * weekly_hours * SESSIONS_PER_MONTH)


def package_monthly_price_millimes_tnd(group_tier, weekly_hours):
    """Tunisia/Konnect equivalent of package_monthly_price_cents — see rate_per_hour_tnd."""
    rate = _rates_by_tier_tnd_millimes().get(group_tier, 0)
    return round(rate * weekly_hours * SESSIONS_PER_MONTH)


def series_monthly_price_cents(series):
    """
    Monthly subscription price for a recurring group. Uses the series'
    committed weekly-hour package if set (the normal case for specialty
    packages); falls back to hourly-rate x this slot's own duration x 4 for
    older data created before packages existed.
    """
    if series.weekly_hours:
        return package_monthly_price_cents(series.group_tier, series.weekly_hours)
    rate = _rates_by_tier().get(series.group_tier, 0)
    duration_hours = series.duration_minutes / 60
    return round(rate * duration_hours * SESSIONS_PER_MONTH)


def reference_monthly_prices_cents(subject, level):
    """
    Reference monthly price per group tier for a subject at a given level,
    based on its official weekly hours (Subject.hours_per_week_*) — this is
    what a family would pay to cover this subject at the same weekly volume
    as a physical lycée, not the price of any specific scheduled group.
    Returns {} if the subject has no reference hours set for that level.
    """
    weekly_hours = subject.hours_per_week_premiere if level == "1ere" else subject.hours_per_week_terminale
    if not weekly_hours:
        return {}
    return {
        tier: round(rate * float(weekly_hours) * SESSIONS_PER_MONTH)
        for tier, rate in _rates_by_tier().items()
    }
