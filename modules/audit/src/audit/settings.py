from pydantic import Field

from shared.settings import BaseAppSettings


class AuditSettings(BaseAppSettings):
    audit_table: str
    exports_bucket: str | None = None   # the listener writes the audit part of a workspace's export there
    audit_retention_days: int = Field(default=365, ge=1)   # TTL removes older entries
