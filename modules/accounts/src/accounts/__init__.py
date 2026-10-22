"""
Accounts module: tenants (workspaces), their users, roles and invitations.

Owns: the accounts table.
Publishes: UserInvited
Listens to: SubscriptionChanged (from billing) -> updates the tenant's plan
Also: the Cognito login trigger that puts tenant_id / role / plan in the token.
"""
