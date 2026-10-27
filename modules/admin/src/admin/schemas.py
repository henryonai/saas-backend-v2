"""What the admin HTTP API accepts and returns."""
from pydantic import BaseModel, Field

from admin.models import AdminAction, WorkspaceSummary


class PlanGrant(BaseModel):
    plan: str
    note: str = Field(default="", max_length=500)   # why: shown in the admin log


class Suspension(BaseModel):
    reason: str = Field(min_length=1, max_length=500)


class WorkspacePage(BaseModel):
    items: list[WorkspaceSummary]
    next_cursor: str | None = None


class AdminLogPage(BaseModel):
    items: list[AdminAction]
    next_cursor: str | None = None
