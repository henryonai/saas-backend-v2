"""
Shared plumbing used by every module. Deployed as a Lambda layer.

Rule: NO business logic here. Only things every module needs in the same way:
reading the login token, error types, publishing events, building the HTTP app,
base settings, and the plan catalog.
"""
