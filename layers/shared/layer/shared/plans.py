"""
Plan catalog: names, prices and limits.

This is shared configuration (not business logic): accounts checks max_users,
orders checks max_orders, billing shows prices. Change numbers here only.
"""

PLANS: dict[str, dict] = {
    "free": {"name": "Free", "price_usd_month": 0, "max_users": 3, "max_orders": 100},
    "pro": {"name": "Pro", "price_usd_month": 29, "max_users": 20, "max_orders": 10_000},
    "business": {"name": "Business", "price_usd_month": 99, "max_users": 200, "max_orders": None},
}

DEFAULT_PLAN = "free"


def is_plan(name: str | None) -> bool:
    return name in PLANS


def get_plan(name: str | None) -> dict:
    """The plan's details. Unknown names fall back to the default plan."""
    return PLANS.get(name or DEFAULT_PLAN, PLANS[DEFAULT_PLAN])


def get_limit(plan: str | None, limit_name: str) -> int | None:
    """Returns the limit for a plan, or None for unlimited."""
    return get_plan(plan)[limit_name]
