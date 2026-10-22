"""
Billing module: plans, checkout, payment webhooks.

Owns: the billing table (subscriptions + processed webhook ids).
It is the source of truth for each tenant's plan.
Publishes: SubscriptionChanged, PaymentFailed (with owner info, so listeners
never need to ask the accounts module who the owner is).
"""
