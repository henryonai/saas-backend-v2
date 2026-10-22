"""
Orders module (example business module).

Owns: the orders table.
Publishes: OrderCreated
Reads the tenant's plan from the login token, so it never calls accounts.
"""
