"""
Billing module: plans, checkout, payment webhooks.

Owns: the billing table (subscriptions + outbox).
It is the source of truth for each tenant's plan.
Publishes: SubscriptionChanged, PaymentFailed (with owner info, so listeners
never need to ask the accounts module who the owner is). SubscriptionChanged
carries a `revision` that increases with every change, so listeners can
ignore events that arrive late.
"""
