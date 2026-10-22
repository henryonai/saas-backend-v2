"""
Notifications module: in-app notifications and email.

Owns: the notifications table.
Listens to: OrderCreated, UserInvited, SubscriptionChanged, PaymentFailed
(EventBridge -> SQS -> worker). Every event carries the data needed, so this
module never calls other modules.
"""
