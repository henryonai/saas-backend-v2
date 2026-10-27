"""
Admin module: the back-office for the people who run the product (the user
pool's platform-admins group), not for a workspace's own admins.

Owns: the admin table (a read model of every workspace, built from events,
and the log of what admins did).
Publishes: PlanGranted, TenantSuspended, TenantUnsuspended.
Listens to: the events that change a workspace's name, plan, status, members or orders.
"""
