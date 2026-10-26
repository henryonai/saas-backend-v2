"""
Plan catalog: names, prices and limits.

This is shared configuration (not business logic): accounts checks max_users,
orders checks max_orders, the assistant checks its monthly allowance, billing
shows prices. Change numbers here only.

The assistant's allowance is per workspace and calendar month (UTC): messages
sent to it, and model tokens (input + output) it used to answer them. Every
plan has a token cap, even Business, so one runaway workspace can't run up the
model bill. Each user also has an hourly limit on messages (the calendar hour,
UTC), so one person can't use up the workspace's month in an afternoon.
Sandbox sessions are the assistant's terminal (its bash tool): one lasts up to
15 minutes of a conversation; 0 means the plan doesn't include it. Uploads
are files a user attaches to messages, per user and calendar hour.
"""

PLANS: dict[str, dict] = {
    "free": {"name": "Free", "price_usd_month": 0, "max_users": 3, "max_orders": 100,
             "assistant_messages_per_month": 50, "assistant_messages_per_user_hour": 20,
             "assistant_tokens_per_month": 500_000, "assistant_sandbox_sessions_per_month": 0,
             "assistant_uploads_per_user_hour": 20},
    "pro": {"name": "Pro", "price_usd_month": 29, "max_users": 20, "max_orders": 10_000,
            "assistant_messages_per_month": 2_000, "assistant_messages_per_user_hour": 60,
            "assistant_tokens_per_month": 20_000_000, "assistant_sandbox_sessions_per_month": 200,
            "assistant_uploads_per_user_hour": 60},
    "business": {"name": "Business", "price_usd_month": 99, "max_users": 200, "max_orders": None,
                 "assistant_messages_per_month": None, "assistant_messages_per_user_hour": 120,
                 "assistant_tokens_per_month": 100_000_000, "assistant_sandbox_sessions_per_month": 2_000,
                 "assistant_uploads_per_user_hour": 120},
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
