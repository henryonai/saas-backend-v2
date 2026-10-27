"""
Audit module: who did what in a workspace.

Owns: the audit table.
Listens to: every event about a workspace (accounts', billing's, orders'),
and writes one entry each. Publishes nothing.
"""
